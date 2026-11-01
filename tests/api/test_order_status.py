"""주문 상태 전이와 고객 취소/반품/구매확정 (R11~R14).

특정 상태의 주문은 QA 도구 주문 자동생성(target_status)으로 바로 만든다.
"""
import pytest

from tests.api.conftest import TISSUE, assert_error

ALLOWED = [
    ("PAID", "PREPARING"), ("PAID", "CANCELED"),
    ("PREPARING", "SHIPPING"), ("PREPARING", "CANCELED"),
    ("SHIPPING", "DELIVERED"),
    ("DELIVERED", "CONFIRMED"), ("DELIVERED", "RETURN_REQUESTED"),
    ("RETURN_REQUESTED", "RETURNED"),
]

NOT_ALLOWED = [
    ("PAID", "SHIPPING"),          # 단계 건너뛰기
    ("PAID", "DELIVERED"),
    ("SHIPPING", "PREPARING"),     # 되돌리기
    ("SHIPPING", "CANCELED"),      # 배송 시작 후 취소
    ("DELIVERED", "PAID"),
    ("CONFIRMED", "RETURNED"),     # 구매확정 후 반품완료 처리
    ("CANCELED", "PAID"),          # 종료 상태에서 이동
    ("RETURNED", "DELIVERED"),
]


def change(client, order_id, status, actor="seller"):
    return client.post(f"/orders/{order_id}/status", json={"status": status, "actor": actor})


@pytest.mark.rule("R12")
@pytest.mark.parametrize("from_status, to_status", ALLOWED)
def test_allowed_transition_is_recorded(shop, client, from_status, to_status):
    """허용된 전이는 성공하고, 마지막 이력에 (이전, 다음, 주체)가 남는다."""
    order = shop.order_in(from_status)
    actor = "carrier" if to_status == "DELIVERED" else "seller"
    # 고객 전용 상태는 고객 API 로 바꾼다
    if to_status == "CONFIRMED":
        res = client.post(f"/orders/{order['id']}/confirm")
        actor = "customer"
    elif to_status == "RETURN_REQUESTED":
        res = client.post(f"/orders/{order['id']}/return")
        actor = "customer"
    else:
        res = change(client, order["id"], to_status, actor)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == to_status
    assert body["history"][-1] | {"changed_at": None} == {
        "from_status": from_status, "to_status": to_status, "actor": actor, "changed_at": None,
    }


@pytest.mark.rule("R12")
@pytest.mark.parametrize("from_status, to_status", NOT_ALLOWED)
def test_not_allowed_transition_rejected(shop, client, from_status, to_status):
    """허용되지 않은 전이는 409 INVALID_STATUS_TRANSITION, 상태와 이력은 그대로."""
    order = shop.order_in(from_status)
    assert_error(change(client, order["id"], to_status), 409, "INVALID_STATUS_TRANSITION")
    after = client.get(f"/orders/{order['id']}").json()
    assert after["status"] == from_status
    assert len(after["history"]) == len(order["history"])


@pytest.mark.rule("R12")
def test_unknown_status_rejected(shop, client):
    """존재하지 않는 상태값은 400 INVALID_STATUS."""
    order = shop.order_in("PAID")
    assert_error(change(client, order["id"], "LOST"), 400, "INVALID_STATUS")


@pytest.mark.rule("R12")
@pytest.mark.parametrize("to_status, endpoint", [("CONFIRMED", "confirm"), ("RETURN_REQUESTED", "return")])
def test_staff_cannot_set_customer_only_status(shop, client, to_status, endpoint):
    """구매확정/반품요청은 고객만 할 수 있다: 판매자 상태변경 API 로는 403 STATUS_NOT_ALLOWED_FOR_ACTOR."""
    order = shop.order_in("DELIVERED")
    assert_error(change(client, order["id"], to_status), 403, "STATUS_NOT_ALLOWED_FOR_ACTOR")
    assert client.get(f"/orders/{order['id']}").json()["status"] == "DELIVERED"


def test_status_change_on_unknown_order(client):
    """없는 주문은 404 ORDER_NOT_FOUND."""
    assert_error(change(client, 9999, "PREPARING"), 404, "ORDER_NOT_FOUND")


@pytest.mark.rule("R13")
@pytest.mark.parametrize("status", ["PAID", "PREPARING"])
def test_customer_can_cancel_before_shipping(shop, client, status):
    """배송 시작 전(결제완료/상품준비중)에는 고객이 취소할 수 있다."""
    order = shop.order_in(status)
    res = client.post(f"/orders/{order['id']}/cancel", json={"reason": "단순 변심"})
    assert res.status_code == 200
    assert res.json()["status"] == "CANCELED"
    assert res.json()["cancel_reason"] == "단순 변심"
    assert res.json()["history"][-1]["actor"] == "customer"


@pytest.mark.rule("R13")
@pytest.mark.parametrize("status", ["SHIPPING", "DELIVERED", "CONFIRMED", "CANCELED", "RETURNED"])
def test_customer_cannot_cancel_after_shipping(shop, client, status):
    """배송 시작 후(또는 종료 상태)에는 409 CANCEL_NOT_ALLOWED."""
    order = shop.order_in(status)
    assert_error(client.post(f"/orders/{order['id']}/cancel"), 409, "CANCEL_NOT_ALLOWED")


@pytest.mark.rule("R14")
@pytest.mark.parametrize("status", ["PAID", "SHIPPING", "CONFIRMED", "RETURN_REQUESTED", "CANCELED"])
def test_return_only_when_delivered(shop, client, status):
    """배송완료가 아닌 주문의 반품 요청은 409 RETURN_NOT_ALLOWED."""
    order = shop.order_in(status)
    assert_error(client.post(f"/orders/{order['id']}/return"), 409, "RETURN_NOT_ALLOWED")


@pytest.mark.rule("R14")
def test_return_after_confirm_rejected(shop, client):
    """구매확정한 뒤에는 반품할 수 없다."""
    order = shop.order_in("DELIVERED")
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert_error(client.post(f"/orders/{order['id']}/return"), 409, "RETURN_NOT_ALLOWED")


@pytest.mark.rule("R12")
@pytest.mark.parametrize("status", ["PAID", "SHIPPING", "CANCELED"])
def test_confirm_only_when_delivered(shop, client, status):
    """배송완료가 아닌 주문의 구매확정은 409 INVALID_STATUS_TRANSITION."""
    order = shop.order_in(status)
    assert_error(client.post(f"/orders/{order['id']}/confirm"), 409, "INVALID_STATUS_TRANSITION")


@pytest.mark.rule("R11")
@pytest.mark.parametrize("final_status", ["CANCELED", "RETURNED"])
def test_cancel_or_return_restores_stock(shop, user, client, final_status):
    """취소/반품완료되면 주문 수량만큼 재고가 돌아온다."""
    before = shop.stock(TISSUE)
    order = shop.order(user, {TISSUE: 3}).json()
    assert shop.stock(TISSUE) == before - 3
    if final_status == "CANCELED":
        client.post(f"/orders/{order['id']}/cancel")
    else:
        for status in ("PREPARING", "SHIPPING", "DELIVERED"):
            change(client, order["id"], status)
        client.post(f"/orders/{order['id']}/return")
        change(client, order["id"], "RETURNED")
    assert client.get(f"/orders/{order['id']}").json()["status"] == final_status
    assert shop.stock(TISSUE) == before


@pytest.mark.rule("R11")
def test_confirmed_order_keeps_stock_and_coupon_used(shop, user, client):
    """구매확정은 재고/쿠폰을 되돌리지 않는다."""
    coupon = shop.coupon(user)
    before = shop.stock(TISSUE)
    order = shop.order(user, {TISSUE: 1}, coupon).json()
    for status in ("PREPARING", "SHIPPING", "DELIVERED"):
        change(client, order["id"], status)
    client.post(f"/orders/{order['id']}/confirm")
    assert shop.stock(TISSUE) == before - 1
    assert shop.coupons(user)[0]["used_order_id"] == order["id"]


@pytest.mark.rule("R11")
def test_returned_order_restores_coupon(shop, user, client):
    """반품완료되면 쿠폰도 미사용으로 돌아온다."""
    coupon = shop.coupon(user)
    order = shop.order(user, {TISSUE: 1}, coupon).json()
    shop._qa("POST", f"/orders/{order['id']}/advance", {"target_status": "RETURNED"})
    assert shop.coupons(user)[0]["used_order_id"] is None
