"""QA 도구 자체 검증 (도구가 만든 데이터가 서비스 규칙과 맞는지).
서비스 API 기능 테스트는 별도 스위트에서 작성한다."""
import pytest

from tests.conftest import QA_HEADERS

FLOW = ["PAID", "PREPARING", "SHIPPING", "DELIVERED", "CONFIRMED"]
SNEAKERS, TUMBLER, BATTERY, TISSUE = 4, 5, 6, 7


def stock_of(client, product_id):
    return client.get(f"/products/{product_id}").json()["stock"]


def test_seed_creates_fixed_products(client):
    products = client.get("/products").json()
    assert len(products) == 8
    assert products[TUMBLER - 1]["stock"] == 0
    assert products[BATTERY - 1]["is_active"] == 0
    assert products[SNEAKERS - 1]["max_per_order"] == 1


def test_seed_resets_previous_data(client, qa):
    qa("/orders/generate", {"count": 2})
    qa("/seed")
    assert client.get("/orders/1").status_code == 404


@pytest.mark.parametrize("scenario", [
    "basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping",
])
def test_generated_orders_follow_amount_rules(qa, scenario):
    orders = qa("/orders/generate", {"count": 10, "scenario": scenario, "seed": 1})

    assert len(orders) == 10
    for o in orders:
        assert o["items_amount"] == sum(i["unit_price"] * i["quantity"] for i in o["items"])
        assert o["shipping_fee"] == (0 if o["items_amount"] >= 19800 else 3000)
        assert o["total_amount"] == o["items_amount"] + o["shipping_fee"] - o["discount_amount"]
        if scenario == "multi_item":
            assert len(o["items"]) >= 2
        if scenario == "coupon_fixed":
            assert o["discount_amount"] == min(3000, o["items_amount"])
        if scenario == "coupon_percent":
            assert o["discount_amount"] == min(o["items_amount"] * 10 // 100, 5000)
        if scenario == "free_shipping":
            assert o["shipping_fee"] == 0
        if scenario == "paid_shipping":
            assert o["shipping_fee"] == 3000


def test_generated_orders_skip_unorderable_products(qa):
    orders = qa("/orders/generate", {"count": 30, "scenario": "multi_item", "seed": 7})
    ordered = {i["product_id"] for o in orders for i in o["items"]}
    assert TUMBLER not in ordered and BATTERY not in ordered
    sneakers = [i for o in orders for i in o["items"] if i["product_id"] == SNEAKERS]
    assert all(i["quantity"] == 1 for i in sneakers)


def test_generate_decreases_stock(client, qa):
    before = {p["id"]: p["stock"] for p in client.get("/products").json()}
    orders = qa("/orders/generate", {"count": 5, "seed": 3})
    after = {p["id"]: p["stock"] for p in client.get("/products").json()}
    for pid in before:
        ordered = sum(i["quantity"] for o in orders for i in o["items"] if i["product_id"] == pid)
        assert after[pid] == before[pid] - ordered


def test_same_seed_reproduces_same_items(qa):
    first = qa("/orders/generate", {"count": 5, "seed": 42})
    qa("/seed")
    second = qa("/orders/generate", {"count": 5, "seed": 42})
    assert [o["items"] for o in first] == [o["items"] for o in second]


def test_generate_to_confirmed_records_every_step(qa):
    order = qa("/orders/generate", {"count": 1, "target_status": "CONFIRMED"})[0]
    assert [h["to_status"] for h in order["history"]] == FLOW
    assert all(h["actor"] == "qa-tool" for h in order["history"])


def test_advance_to_returned_goes_through_delivery(qa):
    order = qa("/orders/generate", {"count": 1, "target_status": "RETURNED"})[0]
    assert [h["to_status"] for h in order["history"]] == FLOW[:4] + ["RETURN_REQUESTED", "RETURNED"]


def test_advance_cannot_go_backward_or_cancel_after_shipping(client, qa):
    order = qa("/orders/generate", {"count": 1, "target_status": "SHIPPING"})[0]
    for target in ("PREPARING", "CANCELED"):
        res = client.post(f"/qa/orders/{order['id']}/advance", json={"target_status": target}, headers=QA_HEADERS)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


@pytest.mark.parametrize("target", ["CANCELED", "RETURNED"])
def test_cancel_and_return_restore_stock_and_coupon(client, qa, target):
    stock_before = stock_of(client, TISSUE)
    user = qa("/users/bulk", {"count": 1})[0]
    coupon = qa("/coupons/issue", {"user_id": user["id"], "discount_type": "FIXED", "value": 1000})
    order = client.post("/orders", json={
        "user_id": user["id"], "items": [{"product_id": TISSUE, "quantity": 2}], "coupon_id": coupon["id"],
    }).json()
    assert stock_of(client, TISSUE) == stock_before - 2

    qa(f"/orders/{order['id']}/advance", {"target_status": target})
    assert stock_of(client, TISSUE) == stock_before
    assert client.get(f"/users/{user['id']}/coupons").json()[0]["used_order_id"] is None


def test_set_stock_makes_product_sold_out(client, qa):
    res = client.put("/qa/products/1/stock", json={"stock": 0}, headers=QA_HEADERS)
    assert res.json()["stock"] == 0
    user = qa("/users/bulk", {"count": 1})[0]
    res = client.post("/orders", json={"user_id": user["id"], "items": [{"product_id": 1, "quantity": 1}]})
    assert res.json()["error"]["code"] == "SOLD_OUT"


def test_fill_cart_then_checkout(client, qa):
    user = qa("/users/bulk", {"count": 1})[0]
    cart = qa("/carts/fill", {"user_id": user["id"], "count": 3, "seed": 1})
    assert len(cart) == 3
    order = client.post("/orders/checkout", json={"user_id": user["id"]}).json()
    assert {i["product_id"] for i in order["items"]} == {c["product_id"] for c in cart}
    assert client.get(f"/users/{user['id']}/cart").json() == []


def test_expired_coupon_can_be_issued_for_testing(client, qa):
    user = qa("/users/bulk", {"count": 1})[0]
    coupon = qa("/coupons/issue", {"user_id": user["id"], "discount_type": "FIXED", "value": 1000, "expires_in_days": -1})
    res = client.post("/orders", json={
        "user_id": user["id"], "items": [{"product_id": 8, "quantity": 1}], "coupon_id": coupon["id"],
    })
    assert res.json()["error"]["code"] == "COUPON_EXPIRED"


def test_qa_api_requires_token(client):
    assert client.post("/qa/seed").status_code == 401
    assert client.post("/qa/seed", headers={"X-QA-Token": "wrong"}).status_code == 401


def test_qa_api_hidden_when_disabled(client, monkeypatch):
    monkeypatch.setenv("QA_TOOLS_ENABLED", "false")
    assert client.post("/qa/seed", headers=QA_HEADERS).status_code == 404
