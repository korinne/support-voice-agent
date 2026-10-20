import pytest
from conftest import D1

from support import store


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [("A-1042", "A-1042"), ("a 1042", "A-1042"), ("1042", "A-1042"), ("A10", None), ("", None)],
)
def test_spoken_order_numbers_are_normalized(spoken, expected):
    assert store.order_id(spoken) == expected


async def test_orders_are_read_only_through_their_customer():
    db = D1()
    assert (await store.get_order(db, "cus_maya", "A-1042"))["items"] == [
        {"name": "Self-Folding Umbrella", "price_cents": 3400}
    ]
    assert await store.get_order(db, "cus_sam", "A-1042") is None
    assert (await store.cancel_order(db, "cus_sam", "A-1061")) is None
    assert (await store.get_order(db, "cus_rosa", "A-1061"))["status"] == "processing"


async def approval(db, amount_cents=1000):
    return await store.create_approval(
        db,
        customer_id="cus_maya",
        order="A-1042",
        call_id="b" * 32,
        amount_cents=amount_cents,
        reason="Bent rib",
    )


def credit(db, customer="cus_maya"):
    return db.rows("SELECT credit_cents FROM customers WHERE id = ?", customer)[0]["credit_cents"]


async def test_an_approval_adds_credit_once():
    db = D1()
    request = await approval(db)
    assert request["id"].startswith("R-") and request["status"] == "pending"
    decided = await store.decide_approval(db, request["id"], approve=True, staff="ana@acme.test")
    assert decided["status"] == "approved"
    assert decided["customer_id"] == "cus_maya" and decided["call_id"] == "b" * 32
    assert credit(db) == 1000
    again = await store.decide_approval(db, request["id"], approve=True, staff="ana@acme.test")
    assert again is None
    assert credit(db) == 1000


async def test_a_declined_request_adds_nothing():
    db = D1()
    request = await approval(db)
    decided = await store.decide_approval(db, request["id"], approve=False, staff="ana@acme.test")
    assert decided["status"] == "declined"
    assert credit(db) == 0
    assert (
        await store.decide_approval(db, request["id"], approve=True, staff="ana@acme.test") is None
    )
    assert credit(db) == 0


async def test_the_desk_lists_pending_requests_first_and_reset_clears_every_account():
    db = D1()
    first = await approval(db)
    await store.decide_approval(db, first["id"], approve=False, staff="ana@acme.test")
    second = await approval(db, 500)
    await store.create_ticket(
        db,
        customer_id="cus_rosa",
        order="A-1033",
        call_id=None,
        summary="Fuel smell",
        details="The pogo stick smells of fuel.",
    )
    overview = await store.desk_overview(db)
    assert [row["id"] for row in overview["approvals"]] == [second["id"], first["id"]]
    assert overview["approvals"][0]["customer_email"] == "maya@example.com"
    assert overview["tickets"][0]["customer_email"] == "rosa@example.com"
    await store.reset_demo(db)
    assert await store.desk_overview(db) == {"approvals": [], "tickets": []}
    assert await store.get_customer(db, "cus_rosa") is None
    assert db.rows("SELECT id FROM orders") == []


async def test_new_accounts_get_their_own_starter_orders():
    db = D1(seeded=False)
    first = await store.ensure_customer(db, "cus_first", name="", email="first@example.com")
    second = await store.ensure_customer(db, "cus_second", name="Sam", email="sam@example.com")
    assert first["credit_cents"] == 0 and second["name"] == "Sam"
    orders = {
        customer: await store.list_orders(db, customer) for customer in ("cus_first", "cus_second")
    }
    for listed in orders.values():
        items = [order["items"][0]["name"] for order in listed]
        assert items == [
            "Instant Tunnel Kit",
            "Self-Folding Umbrella",
            "Spring-Loaded Sneakers",
            "Jet-Powered Pogo Stick, model PS-2",
        ]
        assert [order["total_cents"] for order in listed] == [4500, 3400, 8100, 12900]
    numbers = [order["id"] for listed in orders.values() for order in listed]
    assert len(set(numbers)) == 8
