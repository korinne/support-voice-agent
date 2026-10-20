"""Acme's support tools. Pipecat owns execution and cancellation.

Each handler reads the customer from the call's server-side resources, so the
model can choose what to look up but never whose account it touches.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.resources import AgentResources
from server.observability import exception_fields, log
from support import help_center, store
from support.triage import triage


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HelpArguments(Arguments):
    query: str = Field(min_length=2, max_length=200, description="A few words about the topic")


class OrderArguments(Arguments):
    order_id: str = Field(min_length=1, max_length=20, description="Order number, such as A-1042")


class TicketArguments(Arguments):
    summary: str = Field(min_length=3, max_length=120, description="One line about the problem")
    details: str = Field(
        min_length=3,
        max_length=1200,
        description="What happened, what the customer wants, and whether they asked for a person",
    )
    order_id: str | None = Field(default=None, max_length=20, description="Related order number")


class CreditArguments(Arguments):
    order_id: str = Field(min_length=1, max_length=20, description="Order number, such as A-1042")
    amount: float = Field(gt=0, le=1000, description="Credit in US dollars")
    reason: str = Field(min_length=3, max_length=300, description="Why the customer needs credit")


class ToolError(Exception):
    """An expected failure, reported to the model as an error result."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Outcome:
    result: dict
    # One line for the conversation panel, such as "Opened ticket T-4821".
    summary: str = ""
    sources: tuple[str, ...] = ()


def dollars(cents):
    return f"${cents / 100:,.2f}"


async def owned_order(resources: AgentResources, spoken: str):
    order = store.order_id(spoken)
    found = order and await store.get_order(resources.env.DB, resources.customer_id, order)
    if not found:
        raise ToolError(
            "order_not_found",
            f"No order {spoken!r} on this customer's account. Check the number with them.",
        )
    return found


def describe_order(order):
    return {
        "order_id": order["id"],
        "placed_on": order["placed_on"],
        "status": order["status"],
        "items": [f"{item['name']} ({dollars(item['price_cents'])})" for item in order["items"]],
        "total": dollars(order["total_cents"]),
        "shipping": order["shipping"],
    }


async def account_snapshot(resources: AgentResources) -> dict:
    """The signed-in customer's credit, orders, and recent tickets and credit requests."""
    db, customer_id = resources.env.DB, resources.customer_id
    customer = await store.get_customer(db, customer_id)
    orders = await store.list_orders(db, customer_id)
    tickets = await store.list_tickets(db, customer_id)
    approvals = await store.list_approvals(db, customer_id)
    return {
        "store_credit": dollars(customer["credit_cents"]),
        "orders": [describe_order(order) for order in orders],
        "tickets": [
            {"ticket_id": ticket["id"], "summary": ticket["summary"], "team": ticket["team"]}
            for ticket in tickets[:5]
        ],
        "credit_requests": [
            {
                "request_id": approval["id"],
                "order_id": approval["order_id"],
                "amount": dollars(approval["amount_cents"]),
                "status": approval["status"],
            }
            for approval in approvals[:5]
        ],
    }


async def get_account(_arguments, resources: AgentResources):
    account = await account_snapshot(resources)
    return Outcome(
        {"status": "found", **account},
        summary=f"{len(account['orders'])} orders, {account['store_credit']} store credit",
    )


async def search_help_center(arguments: HelpArguments, resources: AgentResources):
    try:
        results = await help_center.search(resources.env.AI_SEARCH, arguments.query)
    except Exception as exc:
        raise ToolError(
            "help_center_unavailable", "The help center search is unavailable right now."
        ) from exc
    titles = tuple(dict.fromkeys(result["article"] for result in results))
    return Outcome(
        {"status": "found" if results else "empty", "results": results},
        summary=", ".join(titles) if titles else "No matching articles",
        sources=titles,
    )


async def cancellable_order(resources: AgentResources, spoken: str):
    order = await owned_order(resources, spoken)
    if order["status"] != "processing":
        raise ToolError(
            "not_cancellable",
            f"Order {order['id']} is {order['status']} and can't be cancelled. "
            "A delivered order can be returned instead.",
        )
    return order


async def read_back_order(arguments: OrderArguments, resources: AgentResources):
    """What Matt reads back before a cancel. It changes nothing."""
    order = await cancellable_order(resources, arguments.order_id)
    return Outcome(
        {
            "status": "needs_confirmation",
            "order": describe_order(order),
            "next_step": "Nothing is cancelled yet. Read this order back and ask the customer "
            "to confirm. Call cancel_order again only if they say yes.",
        },
        summary=f"Asked the customer to confirm cancelling {order['id']}",
    )


async def cancel_order(arguments: OrderArguments, resources: AgentResources):
    order = await cancellable_order(resources, arguments.order_id)
    order = await store.cancel_order(resources.env.DB, resources.customer_id, order["id"])
    if order["status"] != "cancelled":
        raise ToolError("not_cancellable", f"Order {order['id']} shipped before it was cancelled.")
    return Outcome(
        {"status": "cancelled", "order_id": order["id"], "refund": dollars(order["total_cents"])},
        summary=f"Cancelled order {order['id']}",
    )


async def open_ticket(arguments: TicketArguments, resources: AgentResources):
    order = arguments.order_id and (await owned_order(resources, arguments.order_id))["id"]
    ticket = await store.create_ticket(
        resources.env.DB,
        customer_id=resources.customer_id,
        order=order or None,
        call_id=resources.call_id,
        summary=arguments.summary,
        details=arguments.details,
    )
    resources.background(route_ticket(resources, ticket), "ticket-triage")
    return Outcome(
        {
            "status": "opened",
            "ticket_id": ticket["id"],
            "next_step": "The right Acme team follows up by email; safety issues come first.",
        },
        summary=f"Opened ticket {ticket['id']}",
    )


async def route_ticket(resources: AgentResources, ticket):
    """Sorts a ticket with Clef after the agent has already answered."""
    try:
        result = await triage(resources.env.AI, ticket, resources.gateway)
        await store.save_triage(resources.env.DB, ticket["id"], result)
    except Exception as exc:
        log.bind(call_id=resources.call_id).warning(
            "ticket_triage_failed", tool="open_ticket", **exception_fields(exc)
        )
        return
    resources.notify({"type": "ticket_triaged", "ticket_id": ticket["id"], **result})


async def request_store_credit(arguments: CreditArguments, resources: AgentResources):
    order = await owned_order(resources, arguments.order_id)
    amount_cents = round(arguments.amount * 100)
    if order["status"] not in {"delivered", "returned"}:
        raise ToolError(
            "not_delivered",
            f"Order {order['id']} is {order['status']}. Credit is for delivered items.",
        )
    if amount_cents > order["total_cents"]:
        raise ToolError(
            "amount_too_high",
            f"Credit can't exceed the {dollars(order['total_cents'])} paid for order {order['id']}.",
        )
    approval = await store.create_approval(
        resources.env.DB,
        customer_id=resources.customer_id,
        order=order["id"],
        call_id=resources.call_id,
        amount_cents=amount_cents,
        reason=arguments.reason,
    )
    return Outcome(
        {
            "status": "pending_approval",
            "request_id": approval["id"],
            "order_id": order["id"],
            "amount": dollars(amount_cents),
            "next_step": "A member of the Acme support team approves or declines it. "
            "You'll get a runtime update on this call when they decide.",
        },
        summary=f"Sent {dollars(amount_cents)} credit for staff review",
    )


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    run: Callable[..., Awaitable[Outcome]]
    timeout_seconds: float = 10
    # For an action that can't be undone: returns what to read back to the
    # customer. The tool itself runs only on a call in their next turn.
    confirm: Callable[..., Awaitable[Outcome]] | None = None


TOOLS = (
    Tool(
        "get_account",
        "Look up the signed-in customer's orders, store credit balance, recent tickets, and "
        "credit requests.",
        Arguments,
        get_account,
    ),
    Tool(
        "search_help_center",
        "Search Acme's help center for policies on shipping, returns, damaged items, "
        "warranty, store credit, and product safety. Returns article excerpts or empty.",
        HelpArguments,
        search_help_center,
    ),
    Tool(
        "cancel_order",
        "Cancel one of the customer's orders that hasn't shipped. The first call only returns "
        "the order to read back. Call again after the customer says yes.",
        OrderArguments,
        cancel_order,
        confirm=read_back_order,
    ),
    Tool(
        "open_ticket",
        "Open a support ticket so a person on the Acme team follows up. Use for problems you "
        "can't solve on the call, safety reports, and requests to talk to a person.",
        TicketArguments,
        open_ticket,
    ),
    Tool(
        "request_store_credit",
        "Ask the Acme support team to approve store credit for a damaged or defective item "
        "on a delivered order. The credit is pending until a staff member decides.",
        CreditArguments,
        request_store_credit,
    ),
)


def customer_turns(context) -> int:
    """How many times the customer has spoken. App updates are user messages too."""
    return sum(
        isinstance(message, dict)
        and message.get("role") == "user"
        and not str(message.get("content", "")).startswith("<runtime_context>")
        for message in context.get_messages()
    )


async def run_tool(definition: Tool, arguments, params: FunctionCallParams) -> Outcome:
    """Runs a tool. One with `confirm` returns a read-back instead, and runs only
    when the model calls it again with the same read-back in the customer's next
    turn. The model judges whether the customer said yes; the code makes sure the
    customer heard the read-back and answered before anything changes."""
    resources = params.app_resources
    if definition.confirm is None:
        return await definition.run(arguments, resources)
    read_back = await definition.confirm(arguments, resources)
    key = (definition.name, json.dumps(read_back.result, sort_keys=True))
    turn = customer_turns(params.context)
    if resources.confirmations.pop(key, None) == turn - 1:
        return await definition.run(arguments, resources)
    resources.confirmations[key] = turn
    return read_back


@asynccontextmanager
async def tool_call(params: FunctionCallParams, notify, name: str):
    """Report a tool's lifecycle through execution and result delivery."""
    event = {"name": name, "call_id": params.tool_call_id}
    context = getattr(params, "app_resources", None)
    tool_log = log.bind(
        call_id=getattr(context, "call_id", None), tool=name, tool_call_id=params.tool_call_id
    )
    notify({"type": "tool_started", **event})

    async def complete(result: dict, *, summary="", sources=()):
        await params.result_callback(result)
        if "error" in result:
            outcome = "tool_failed"
        elif result.get("status") == "empty":
            outcome = "tool_empty"
        else:
            outcome = "tool_finished"
        update = {"type": outcome, "code": result.get("code"), **event}
        if summary:
            update["summary"] = summary[:160]
        if sources:
            update["sources"] = [{"title": title[:160]} for title in sources[:6]]
        notify(update)
        if "error" in result:
            tool_log.warning("tool_failed", error_code=result.get("code"))

    try:
        yield complete
    except asyncio.CancelledError:
        notify({"type": "tool_cancelled", **event})
        raise
    except Exception as exc:
        notify({"type": "tool_failed", **event})
        tool_log.error("tool_failed", **exception_fields(exc))
        raise


def _register(definition: Tool, notify) -> FunctionSchema:
    @tool_options(timeout_secs=definition.timeout_seconds)
    async def execute(params: FunctionCallParams):
        async with tool_call(params, notify, definition.name) as complete:
            try:
                arguments = definition.arguments.model_validate(params.arguments)
            except ValidationError:
                await complete(
                    {
                        "status": "error",
                        "code": "invalid_arguments",
                        "error": "Arguments did not match this tool's schema. Check required "
                        "fields and limits.",
                    }
                )
                return
            try:
                outcome = await run_tool(definition, arguments, params)
            except ToolError as exc:
                await complete({"status": "error", "code": exc.code, "error": str(exc)})
                return
            except Exception as exc:
                log.bind(call_id=params.app_resources.call_id).error(
                    "tool_failed", tool=definition.name, **exception_fields(exc)
                )
                await complete(
                    {
                        "status": "error",
                        "code": type(exc).__name__,
                        "error": "The action failed and did not happen.",
                    }
                )
                return
            await complete(outcome.result, summary=outcome.summary, sources=outcome.sources)

    schema = definition.arguments.model_json_schema()
    return FunctionSchema(
        name=definition.name,
        description=definition.description,
        properties=schema.get("properties", {}),
        required=schema.get("required", []),
        handler=execute,
    )


def build_tools(notify):
    return [_register(definition, notify) for definition in TOOLS]
