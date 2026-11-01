"""주문 가능 여부: 판매중지/품절/재고/구매제한/수량 (R1~R6)."""
import pytest

from tests.api.conftest import BATTERY, CABLE, PEN, SNEAKERS, TUMBLER, WATER, assert_error


@pytest.mark.rule("R1")
def test_inactive_product_cannot_be_ordered(shop, user):
    """판매중지 상품 주문은 400 PRODUCT_INACTIVE."""
    assert_error(shop.order(user, {BATTERY: 1}), 400, "PRODUCT_INACTIVE")


@pytest.mark.rule("R1")
def test_inactive_check_comes_before_stock_check(shop, user):
    """판매중지 + 품절인 상품은 판매중지 에러가 먼저 난다 (검사 순서)."""
    product = shop.new_product(price=1000, stock=0, is_active=False)
    assert_error(shop.order(user, {product: 1}), 400, "PRODUCT_INACTIVE")


@pytest.mark.rule("R2")
def test_sold_out_product_cannot_be_ordered(shop, user):
    """재고 0 상품 주문은 409 SOLD_OUT."""
    assert_error(shop.order(user, {TUMBLER: 1}), 409, "SOLD_OUT")


@pytest.mark.rule("R2")
def test_product_becomes_sold_out_when_stock_set_to_zero(shop, user):
    """QA 도구로 재고를 0 으로 바꾸면 바로 품절 처리된다."""
    shop.set_stock(CABLE, 0)
    assert_error(shop.order(user, {CABLE: 1}), 409, "SOLD_OUT")


@pytest.mark.rule("R3")
def test_order_exactly_all_remaining_stock(shop, user):
    """재고 = 주문수량이면 주문되고 재고가 0 이 된다. 그다음 주문은 품절."""
    shop.set_stock(CABLE, 2)
    assert shop.order(user, {CABLE: 2}).status_code == 201
    assert shop.stock(CABLE) == 0
    assert_error(shop.order(user, {CABLE: 1}), 409, "SOLD_OUT")


@pytest.mark.rule("R3")
def test_order_more_than_stock(shop, user):
    """재고 < 주문수량이면 409 INSUFFICIENT_STOCK, 재고는 그대로."""
    shop.set_stock(CABLE, 2)
    assert_error(shop.order(user, {CABLE: 3}), 409, "INSUFFICIENT_STOCK")
    assert shop.stock(CABLE) == 2


@pytest.mark.rule("R4")
@pytest.mark.parametrize("quantity, ok", [(1, True), (2, False)])
def test_purchase_limit_boundary(shop, user, quantity, ok):
    """1회 1개 제한 상품: 1개는 주문, 2개는 400 PURCHASE_LIMIT_EXCEEDED."""
    res = shop.order(user, {SNEAKERS: quantity})
    if ok:
        assert res.status_code == 201
    else:
        assert_error(res, 400, "PURCHASE_LIMIT_EXCEEDED")


@pytest.mark.rule("R4")
@pytest.mark.rule("R5")
def test_purchase_limit_applies_to_merged_lines(shop, user):
    """같은 상품을 1개씩 두 줄로 나눠 담아도 합친 수량(2)으로 구매제한을 검사한다."""
    assert_error(shop.order(user, [(SNEAKERS, 1), (SNEAKERS, 1)]), 400, "PURCHASE_LIMIT_EXCEEDED")


@pytest.mark.rule("R5")
@pytest.mark.parametrize("quantity, ok", [(0, False), (1, True), (99, True), (100, False), (-1, False)])
def test_quantity_boundary(shop, user, quantity, ok):
    """수량 경계값: 1~99 만 주문, 0/100/음수는 400 INVALID_QUANTITY."""
    res = shop.order(user, {PEN: quantity})
    if ok:
        assert res.status_code == 201
        assert res.json()["items"][0]["quantity"] == quantity
    else:
        assert_error(res, 400, "INVALID_QUANTITY")


@pytest.mark.rule("R5")
def test_merged_lines_over_99_rejected(shop, user):
    """한 줄씩은 99 이하여도 같은 상품 합계가 100 이면 INVALID_QUANTITY."""
    assert_error(shop.order(user, [(PEN, 50), (PEN, 50)]), 400, "INVALID_QUANTITY")


@pytest.mark.rule("R5")
def test_merged_lines_become_one_order_item(shop, user):
    """같은 상품 두 줄은 주문 상품 한 줄로 합쳐진다."""
    res = shop.order(user, [(PEN, 2), (PEN, 3)])
    assert res.status_code == 201
    assert [(i["product_id"], i["quantity"]) for i in res.json()["items"]] == [(PEN, 5)]


@pytest.mark.parametrize("items, status_code, code", [
    ({}, 400, "EMPTY_ORDER"),
    ({999: 1}, 404, "PRODUCT_NOT_FOUND"),
])
def test_invalid_order_requests(shop, user, items, status_code, code):
    """빈 주문, 없는 상품."""
    assert_error(shop.order(user, items), status_code, code)


def test_unknown_user_cannot_order(shop):
    """없는 회원은 404 USER_NOT_FOUND."""
    assert_error(shop.order(9999, {PEN: 1}), 404, "USER_NOT_FOUND")


@pytest.mark.rule("R6")
def test_order_decreases_stock_per_product(shop, user):
    """주문 수량만큼 상품별 재고가 줄어든다."""
    before = {pid: shop.stock(pid) for pid in (PEN, WATER)}
    assert shop.order(user, {PEN: 3, WATER: 2}).status_code == 201
    assert shop.stock(PEN) == before[PEN] - 3
    assert shop.stock(WATER) == before[WATER] - 2


@pytest.mark.rule("R6")
def test_failed_order_does_not_change_any_stock(shop, user):
    """여러 상품 중 하나라도 실패하면 다른 상품 재고도 줄지 않는다 (전부 아니면 전무)."""
    before = shop.stock(PEN)
    assert_error(shop.order(user, {PEN: 3, TUMBLER: 1}), 409, "SOLD_OUT")
    assert shop.stock(PEN) == before
