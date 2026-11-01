"""QA 도구 자체 검증 (도구가 만든 데이터가 서비스 규칙과 맞는지).
서비스 API 기능 테스트는 별도 스위트에서 작성한다."""
import pytest

from tests.conftest import QA_HEADERS

FLOW = ["PENDING", "ACCEPTED", "COOKING", "DELIVERING", "DELIVERED"]


def test_seed_creates_fixed_stores(client):
    stores = client.get("/stores").json()
    assert [s["name"] for s in stores] == ["QA치킨", "QA분식", "QA피자", "QA카페"]


def test_seed_resets_previous_data(client, qa):
    qa("/orders/generate", {"count": 2})
    qa("/seed")
    assert client.get("/orders/1").status_code == 404


@pytest.mark.parametrize("scenario", ["basic", "multi_item", "coupon_fixed", "coupon_percent"])
def test_generated_orders_follow_amount_rules(client, qa, scenario):
    stores = {s["id"]: s for s in client.get("/stores").json()}
    orders = qa("/orders/generate", {"count": 10, "scenario": scenario, "seed": 1})

    assert len(orders) == 10
    for o in orders:
        store = stores[o["store_id"]]
        assert store["is_open"]
        assert o["items_amount"] == sum(i["unit_price"] * i["quantity"] for i in o["items"])
        assert o["items_amount"] >= store["min_order_amount"]
        assert o["total_amount"] == o["items_amount"] + o["delivery_fee"] - o["discount_amount"]
        if scenario == "multi_item":
            assert len(o["items"]) >= 2
        if scenario == "coupon_fixed":
            assert o["discount_amount"] == 3000
        if scenario == "coupon_percent":
            assert o["discount_amount"] == min(o["items_amount"] * 10 // 100, 5000)


def test_generated_orders_never_contain_sold_out_menu(qa):
    orders = qa("/orders/generate", {"count": 30, "store_id": 1, "seed": 7})
    assert all(i["menu_name"] != "치즈볼" for o in orders for i in o["items"])


def test_same_seed_reproduces_same_items(qa):
    first = qa("/orders/generate", {"count": 5, "seed": 42})
    second = qa("/orders/generate", {"count": 5, "seed": 42})
    assert [o["items"] for o in first] == [o["items"] for o in second]


def test_generate_to_delivered_records_every_step(qa):
    order = qa("/orders/generate", {"count": 1, "target_status": "DELIVERED"})[0]
    assert order["status"] == "DELIVERED"
    assert [h["to_status"] for h in order["history"]] == FLOW
    assert all(h["actor"] == "qa-tool" for h in order["history"])


def test_advance_from_middle_status(qa):
    order = qa("/orders/generate", {"count": 1, "target_status": "ACCEPTED"})[0]
    order = qa(f"/orders/{order['id']}/advance", {"target_status": "DELIVERING"})
    assert [h["to_status"] for h in order["history"]] == FLOW[:4]


def test_advance_cannot_go_backward(client, qa):
    order = qa("/orders/generate", {"count": 1, "target_status": "DELIVERED"})[0]
    res = client.post(f"/qa/orders/{order['id']}/advance", json={"target_status": "COOKING"}, headers=QA_HEADERS)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


def test_canceled_coupon_order_releases_coupon(client, qa):
    order = qa("/orders/generate", {"count": 1, "scenario": "coupon_fixed", "target_status": "CANCELED"})[0]
    coupons = client.get(f"/users/{order['user_id']}/coupons").json()
    assert coupons[0]["used_order_id"] is None


def test_generate_rejects_closed_store(client):
    res = client.post("/qa/orders/generate", json={"count": 1, "store_id": 3}, headers=QA_HEADERS)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "STORE_CLOSED"


def test_expired_coupon_can_be_issued_for_testing(client, qa):
    user = qa("/users/bulk", {"count": 1})[0]
    coupon = qa("/coupons/issue", {"user_id": user["id"], "discount_type": "FIXED", "value": 1000, "expires_in_days": -1})
    res = client.post("/orders", json={
        "user_id": user["id"], "store_id": 4, "items": [{"menu_id": 11, "quantity": 1}], "coupon_id": coupon["id"],
    })
    assert res.json()["error"]["code"] == "COUPON_EXPIRED"


def test_qa_api_requires_token(client):
    assert client.post("/qa/seed").status_code == 401
    assert client.post("/qa/seed", headers={"X-QA-Token": "wrong"}).status_code == 401


def test_qa_api_hidden_when_disabled(client, monkeypatch):
    monkeypatch.setenv("QA_TOOLS_ENABLED", "false")
    assert client.post("/qa/seed", headers=QA_HEADERS).status_code == 404
