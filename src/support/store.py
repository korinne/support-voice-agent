"""Acme's support records in D1.

Every function that reads or changes a customer's data takes that customer's
ID from the signed-in session. The language model never supplies it.
"""

import json
import re
import secrets
from datetime import UTC, datetime, timedelta

ORDER_COLUMNS = "id, placed_on, status, items, total_cents, shipping"
TICKET_COLUMNS = (
    "id, order_id, summary, created_at, team, team_confidence, urgency, wants_person, triaged_at"
)
APPROVAL_COLUMNS = "id, action, order_id, amount_cents, reason, status, decided_at, created_at"


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def order_id(spoken: str) -> str | None:
    """Normalizes a transcribed order number such as "a 1042" or "A1042" to A-1042."""
    digits = re.sub(r"\D", "", spoken)
    return f"A-{digits}" if len(digits) == 4 else None


def _rows(result) -> list[dict]:
    return [dict(row) for row in (result or {}).get("results") or []]


def _order(row):
    order = dict(row)
    order["items"] = json.loads(order["items"])
    return order


async def _insert(db, prefix, sql, params):
    """Inserts with a short, speakable ID, retrying on the rare collision."""
    for _ in range(5):
        record_id = f"{prefix}-{secrets.randbelow(9000) + 1000}"
        row = await db.prepare(sql).bind(record_id, *params).first()
        if row:
            return dict(row)
    raise RuntimeError("Could not allocate a record ID")


def starter_orders(today):
    """The orders every new account starts with, so anyone can bring any demo problem.

    Dates count back from the day the account is created, so a delivery stays
    inside the 14-day damage window and the sneakers stay stuck in transit.
    """

    def day(days_ago):
        return (today - timedelta(days=days_ago)).isoformat()

    return [
        ("delivered", day(6), [("Self-Folding Umbrella", 3400)], f"Delivered {day(2)}"),
        (
            "shipped",
            day(9),
            [("Spring-Loaded Sneakers", 7200), ("Glow-in-the-Dark Map", 900)],
            f"In transit. Last carrier scan {day(5)} in Albuquerque, NM",
        ),
        (
            "delivered",
            day(20),
            [("Jet-Powered Pogo Stick, model PS-2", 12900)],
            f"Delivered {day(15)}",
        ),
        ("processing", day(0), [("Instant Tunnel Kit", 4500)], "Not shipped yet"),
    ]


async def check(db):
    """Fails when the tables don't exist, usually because migrations haven't run."""
    await db.prepare("SELECT 1 FROM customers LIMIT 1").first()


async def ensure_customer(db, customer_id, *, name, email):
    """Returns the customer, creating the account and its starter orders on first sign-in."""
    for _ in range(5):
        if customer := await get_customer(db, customer_id):
            return customer
        orders = starter_orders(datetime.now(UTC).date())
        numbers = secrets.SystemRandom().sample(range(1000, 10000), len(orders))
        statements = [
            db.prepare("INSERT INTO customers (id, name, email) VALUES (?, ?, ?)").bind(
                customer_id, name, email
            )
        ]
        for number, (status, placed_on, items, shipping) in zip(numbers, orders, strict=True):
            statements.append(
                db.prepare(
                    "INSERT INTO orders "
                    "(id, customer_id, placed_on, status, items, total_cents, shipping) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)"
                ).bind(
                    f"A-{number}",
                    customer_id,
                    placed_on,
                    status,
                    json.dumps([{"name": item, "price_cents": cents} for item, cents in items]),
                    sum(cents for _, cents in items),
                    shipping,
                )
            )
        try:
            # One batch is one transaction: the account and its orders appear together.
            await db.batch(statements)
        except Exception:
            # Another request created the account first, or an order number was taken.
            continue
    raise RuntimeError("Could not create the customer's account")


async def get_customer(db, customer_id):
    row = await (
        db.prepare("SELECT id, name, email, credit_cents FROM customers WHERE id = ?")
        .bind(customer_id)
        .first()
    )
    return dict(row) if row else None


async def list_orders(db, customer_id):
    result = await (
        db.prepare(
            f"SELECT {ORDER_COLUMNS} FROM orders WHERE customer_id = ? ORDER BY placed_on DESC"
        )
        .bind(customer_id)
        .all()
    )
    return [_order(row) for row in _rows(result)]


async def get_order(db, customer_id, order):
    row = await (
        db.prepare(f"SELECT {ORDER_COLUMNS} FROM orders WHERE id = ? AND customer_id = ?")
        .bind(order, customer_id)
        .first()
    )
    return _order(row) if row else None


async def cancel_order(db, customer_id, order):
    """Cancels an order that hasn't shipped. Returns the order as it is afterwards."""
    await (
        db.prepare(
            "UPDATE orders SET status = 'cancelled', shipping = 'Cancelled before shipping' "
            "WHERE id = ? AND customer_id = ? AND status = 'processing'"
        )
        .bind(order, customer_id)
        .run()
    )
    return await get_order(db, customer_id, order)


async def create_ticket(db, *, customer_id, order, call_id, summary, details):
    return await _insert(
        db,
        "T",
        "INSERT INTO tickets (id, customer_id, order_id, call_id, summary, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING "
        f"RETURNING {TICKET_COLUMNS}, details",
        (customer_id, order, call_id, summary, details, now()),
    )


async def save_triage(db, ticket_id, triage):
    await (
        db.prepare(
            "UPDATE tickets SET team = ?, team_confidence = ?, urgency = ?, wants_person = ?, "
            "triaged_at = ? WHERE id = ?"
        )
        .bind(
            triage["team"],
            triage["team_confidence"],
            triage["urgency"],
            triage["wants_person"],
            now(),
            ticket_id,
        )
        .run()
    )


async def list_tickets(db, customer_id):
    result = await (
        db.prepare(
            f"SELECT {TICKET_COLUMNS} FROM tickets WHERE customer_id = ? "
            "ORDER BY created_at DESC LIMIT 20"
        )
        .bind(customer_id)
        .all()
    )
    return _rows(result)


async def create_approval(db, *, customer_id, order, call_id, amount_cents, reason):
    return await _insert(
        db,
        "R",
        "INSERT INTO approvals "
        "(id, action, customer_id, order_id, call_id, amount_cents, reason, created_at) "
        "VALUES (?, 'store_credit', ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING "
        f"RETURNING {APPROVAL_COLUMNS}",
        (customer_id, order, call_id, amount_cents, reason, now()),
    )


async def list_approvals(db, customer_id):
    result = await (
        db.prepare(
            f"SELECT {APPROVAL_COLUMNS} FROM approvals WHERE customer_id = ? "
            "ORDER BY created_at DESC LIMIT 20"
        )
        .bind(customer_id)
        .all()
    )
    return _rows(result)


async def decide_approval(db, approval_id, *, approve: bool, staff: str):
    """Records a staff decision once. Returns None if the request was already decided.

    Both statements run in one transaction. The credit is added only while the
    request is still pending, so a second click can't add it twice.
    """
    status = "approved" if approve else "declined"
    pending = "SELECT {} FROM approvals WHERE id = ?1 AND status = 'pending'"
    _, decided = await db.batch(
        [
            db.prepare(
                "UPDATE customers SET credit_cents = credit_cents + "
                f"({pending.format('amount_cents')}) "
                f"WHERE id = ({pending.format('customer_id')}) AND ?2 = 'approved'"
            ).bind(approval_id, status),
            db.prepare(
                "UPDATE approvals SET status = ?2, decided_by = ?3, decided_at = ?4 "
                "WHERE id = ?1 AND status = 'pending' "
                f"RETURNING {APPROVAL_COLUMNS}, customer_id, call_id"
            ).bind(approval_id, status, staff, now()),
        ]
    )
    rows = _rows(decided)
    return rows[0] if rows else None


async def desk_overview(db):
    """Everything the staff desk shows, across all customers."""
    approvals, tickets = await db.batch(
        [
            db.prepare(
                "SELECT a.id, a.action, a.order_id, a.amount_cents, a.reason, a.status, "
                "a.decided_by, a.decided_at, a.created_at, "
                "c.name AS customer_name, c.email AS customer_email "
                "FROM approvals a JOIN customers c ON c.id = a.customer_id "
                "ORDER BY a.status = 'pending' DESC, a.created_at DESC LIMIT 30"
            ),
            db.prepare(
                "SELECT t.id, t.order_id, t.summary, t.details, t.created_at, t.team, "
                "t.team_confidence, t.urgency, t.wants_person, t.triaged_at, "
                "c.name AS customer_name, c.email AS customer_email "
                "FROM tickets t JOIN customers c ON c.id = t.customer_id "
                "ORDER BY t.created_at DESC LIMIT 30"
            ),
        ]
    )
    return {"approvals": _rows(approvals), "tickets": _rows(tickets)}


async def reset_demo(db):
    """Removes every account. Each person gets fresh starter orders on their next visit."""
    # One batch is one transaction, so a failed reset leaves the old data intact.
    await db.batch(
        [
            db.prepare(f"DELETE FROM {table}")
            for table in ("approvals", "tickets", "orders", "customers")
        ]
    )
