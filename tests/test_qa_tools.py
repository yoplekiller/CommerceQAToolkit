"""QA 도구 자체 검증 (도구가 만든 데이터가 서비스 규칙과 맞는지).
서비스 API 기능 테스트는 별도 스위트에서 작성한다."""
import pytest

from tests.conftest import QA_HEADERS

FLOW = ["PAID", "PREPARING", "SHIPPING", "DELIVERED", "CONFIRMED"]
SNEAKERS, TUMBLER, BATTERY, TISSUE = 4, 5, 6, 7


def stock_of(client, product_id):
    """상품의 현재 재고."""
    return client.get(f"/products/{product_id}").json()["stock"]


def test_seed_creates_fixed_products(client):
    """시드 상품 8종이 품절/판매중지/구매제한 특성대로 만들어진다."""
    products = client.get("/products").json()
    assert len(products) == 8
    assert products[TUMBLER - 1]["stock"] == 0
    assert products[BATTERY - 1]["is_active"] == 0
    assert products[SNEAKERS - 1]["max_per_order"] == 1


def test_seed_resets_previous_data(client, qa):
    """seed 를 다시 실행하면 이전 주문이 모두 지워진다."""
    qa("/orders/generate", {"count": 2})
    qa("/seed")
    assert client.get("/orders/1").status_code == 404


@pytest.mark.parametrize("scenario", [
    "basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping",
])
def test_generated_orders_follow_amount_rules(qa, scenario):
    """시나리오별 자동생성 주문의 금액이 배송비/할인/결제금액 규칙(R7, R9, R10)과 맞는다."""
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
    """자동생성 주문에 품절/판매중지 상품이 없고, 구매 제한 상품은 제한 수량을 넘지 않는다."""
    orders = qa("/orders/generate", {"count": 30, "scenario": "multi_item", "seed": 7})
    ordered = {i["product_id"] for o in orders for i in o["items"]}
    assert TUMBLER not in ordered and BATTERY not in ordered
    sneakers = [i for o in orders for i in o["items"] if i["product_id"] == SNEAKERS]
    assert all(i["quantity"] == 1 for i in sneakers)


def test_generate_decreases_stock(client, qa):
    """자동생성한 주문 수량만큼 재고가 줄어든다 (R6)."""
    before = {p["id"]: p["stock"] for p in client.get("/products").json()}
    orders = qa("/orders/generate", {"count": 5, "seed": 3})
    after = {p["id"]: p["stock"] for p in client.get("/products").json()}
    for pid in before:
        ordered = sum(i["quantity"] for o in orders for i in o["items"] if i["product_id"] == pid)
        assert after[pid] == before[pid] - ordered


def test_same_seed_reproduces_same_items(qa):
    """같은 seed 값이면 같은 상품 조합의 주문이 만들어진다."""
    first = qa("/orders/generate", {"count": 5, "seed": 42})
    qa("/seed")
    second = qa("/orders/generate", {"count": 5, "seed": 42})
    assert [o["items"] for o in first] == [o["items"] for o in second]


def test_generate_to_confirmed_records_every_step(qa):
    """구매확정까지 자동 진행하면 모든 단계가 qa-tool 이력으로 남는다."""
    order = qa("/orders/generate", {"count": 1, "target_status": "CONFIRMED"})[0]
    assert [h["to_status"] for h in order["history"]] == FLOW
    assert all(h["actor"] == "qa-tool" for h in order["history"])


def test_advance_to_returned_goes_through_delivery(qa):
    """반품완료로 자동 진행하면 배송완료를 거쳐 반품 단계로 간다."""
    order = qa("/orders/generate", {"count": 1, "target_status": "RETURNED"})[0]
    assert [h["to_status"] for h in order["history"]] == FLOW[:4] + ["RETURN_REQUESTED", "RETURNED"]


def test_advance_cannot_go_backward_or_cancel_after_shipping(client, qa):
    """배송중 주문은 이전 상태로 되돌리거나 취소할 수 없다 (R12, R13)."""
    order = qa("/orders/generate", {"count": 1, "target_status": "SHIPPING"})[0]
    for target in ("PREPARING", "CANCELED"):
        res = client.post(f"/qa/orders/{order['id']}/advance", json={"target_status": target}, headers=QA_HEADERS)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


@pytest.mark.parametrize("target", ["CANCELED", "RETURNED"])
def test_cancel_and_return_restore_stock_and_coupon(client, qa, target):
    """취소/반품완료 시 재고와 쿠폰이 복구된다 (R11)."""
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
    """재고를 0 으로 바꾸면 그 상품 주문이 SOLD_OUT 으로 거부된다."""
    res = client.put("/qa/products/1/stock", json={"stock": 0}, headers=QA_HEADERS)
    assert res.json()["stock"] == 0
    user = qa("/users/bulk", {"count": 1})[0]
    res = client.post("/orders", json={"user_id": user["id"], "items": [{"product_id": 1, "quantity": 1}]})
    assert res.json()["error"]["code"] == "SOLD_OUT"


def test_fill_cart_then_checkout(client, qa):
    """자동으로 담은 장바구니를 주문하면 담긴 상품이 그대로 주문되고 장바구니가 비워진다."""
    user = qa("/users/bulk", {"count": 1})[0]
    cart = qa("/carts/fill", {"user_id": user["id"], "count": 3, "seed": 1})
    assert len(cart) == 3
    order = client.post("/orders/checkout", json={"user_id": user["id"]}).json()
    assert {i["product_id"] for i in order["items"]} == {c["product_id"] for c in cart}
    assert client.get(f"/users/{user['id']}/cart").json() == []


def test_expired_coupon_can_be_issued_for_testing(client, qa):
    """만료일을 음수로 발급한 쿠폰은 주문에 쓰면 COUPON_EXPIRED 가 된다."""
    user = qa("/users/bulk", {"count": 1})[0]
    coupon = qa("/coupons/issue", {"user_id": user["id"], "discount_type": "FIXED", "value": 1000, "expires_in_days": -1})
    res = client.post("/orders", json={
        "user_id": user["id"], "items": [{"product_id": 8, "quantity": 1}], "coupon_id": coupon["id"],
    })
    assert res.json()["error"]["code"] == "COUPON_EXPIRED"


def test_qa_api_requires_token(client):
    """QA 도구 API 는 토큰이 없거나 틀리면 401."""
    assert client.post("/qa/seed").status_code == 401
    assert client.post("/qa/seed", headers={"X-QA-Token": "wrong"}).status_code == 401


def test_qa_api_hidden_when_disabled(client, monkeypatch):
    """QA_TOOLS_ENABLED=false 면 QA 도구 API 가 404."""
    monkeypatch.setenv("QA_TOOLS_ENABLED", "false")
    assert client.post("/qa/seed", headers=QA_HEADERS).status_code == 404
