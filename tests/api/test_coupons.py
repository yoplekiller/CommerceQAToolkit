"""쿠폰 사용 조건과 복구 (R8, R11)."""
import pytest

from tests.api.conftest import PEN, TISSUE, assert_error


@pytest.mark.rule("R8")
def test_coupon_of_other_user_rejected(shop, user):
    """다른 회원의 쿠폰은 403 COUPON_NOT_OWNED."""
    other = shop.new_user()
    coupon = shop.coupon(other)
    assert_error(shop.order(user, {PEN: 1}, coupon), 403, "COUPON_NOT_OWNED")


@pytest.mark.rule("R8")
def test_expired_coupon_rejected(shop, user):
    """만료 쿠폰(QA 도구로 만료일 -1일 발급)은 400 COUPON_EXPIRED."""
    coupon = shop.coupon(user, expires_in_days=-1)
    assert_error(shop.order(user, {PEN: 1}, coupon), 400, "COUPON_EXPIRED")


@pytest.mark.rule("R8")
def test_used_coupon_rejected(shop, user):
    """한 번 쓴 쿠폰을 다시 쓰면 409 COUPON_ALREADY_USED."""
    coupon = shop.coupon(user)
    assert shop.order(user, {PEN: 1}, coupon).status_code == 201
    assert_error(shop.order(user, {PEN: 1}, coupon), 409, "COUPON_ALREADY_USED")


@pytest.mark.rule("R8")
def test_unknown_coupon_rejected(shop, user):
    """없는 쿠폰은 404 COUPON_NOT_FOUND."""
    assert_error(shop.order(user, {PEN: 1}, 9999), 404, "COUPON_NOT_FOUND")


@pytest.mark.rule("R8")
@pytest.mark.parametrize("price, ok", [(19799, False), (19800, True)])
def test_coupon_min_order_amount_boundary(shop, user, price, ok):
    """쿠폰 최소주문금액 19,800원 경계: 19,799원 주문은 400 COUPON_MIN_ORDER_NOT_MET."""
    product = shop.new_product(price=price)
    coupon = shop.coupon(user, min_order_amount=19800)
    res = shop.order(user, {product: 1}, coupon)
    if ok:
        assert res.status_code == 201
    else:
        assert_error(res, 400, "COUPON_MIN_ORDER_NOT_MET")


@pytest.mark.rule("R8")
def test_rejected_coupon_order_keeps_stock_and_coupon(shop, user):
    """쿠폰 검증에 실패한 주문은 재고도 안 줄고 쿠폰도 미사용 상태로 남는다."""
    coupon = shop.coupon(user, min_order_amount=50000)
    before = shop.stock(PEN)
    assert_error(shop.order(user, {PEN: 1}, coupon), 400, "COUPON_MIN_ORDER_NOT_MET")
    assert shop.stock(PEN) == before
    assert shop.coupons(user)[0]["used_order_id"] is None


@pytest.mark.rule("R11")
def test_canceled_order_coupon_can_be_reused(shop, user, client):
    """취소한 주문에 쓴 쿠폰은 다시 쓸 수 있다."""
    coupon = shop.coupon(user)
    order = shop.order(user, {TISSUE: 1}, coupon).json()
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 200
    assert shop.order(user, {TISSUE: 1}, coupon).status_code == 201
