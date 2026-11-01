"""결제금액 계산: 배송비/쿠폰 할인/결제금액 (R7, R9, R10)."""
import pytest

from tests.api.conftest import CABLE, EARPHONE, PEN, TISSUE, WATER


def amounts(res) -> tuple[int, int, int, int]:
    """(상품합계, 배송비, 할인, 결제금액)"""
    assert res.status_code == 201, res.text
    o = res.json()
    return o["items_amount"], o["shipping_fee"], o["discount_amount"], o["total_amount"]


@pytest.mark.rule("R7")
@pytest.mark.rule("R10")
@pytest.mark.parametrize("price, shipping_fee", [(19799, 3000), (19800, 0), (19801, 0)])
def test_free_shipping_boundary(shop, user, price, shipping_fee):
    """무료배송 경계값: 19,799원은 배송비 3,000원, 19,800원부터 0원."""
    product = shop.new_product(price=price)
    assert amounts(shop.order(user, {product: 1})) == (price, shipping_fee, 0, price + shipping_fee)


@pytest.mark.rule("R7")
def test_seed_product_at_threshold_ships_free(shop, user):
    """시드의 화장지(19,800원) 1개 주문은 무료배송."""
    assert amounts(shop.order(user, {TISSUE: 1})) == (19800, 0, 0, 19800)


@pytest.mark.rule("R7")
def test_free_shipping_counts_sum_of_items(shop, user):
    """여러 상품 합계로 기준을 넘으면 무료배송: 9,900 x 2 = 19,800."""
    assert amounts(shop.order(user, {CABLE: 2})) == (19800, 0, 0, 19800)


@pytest.mark.rule("R7")
@pytest.mark.rule("R10")
def test_free_shipping_uses_amount_before_coupon(shop, user):
    """무료배송 기준은 쿠폰 할인 전 금액: 19,800원 - 3,000원 쿠폰이어도 배송비 0원."""
    coupon = shop.coupon(user, "FIXED", 3000)
    assert amounts(shop.order(user, {TISSUE: 1}, coupon)) == (19800, 0, 3000, 16800)


@pytest.mark.rule("R9")
@pytest.mark.rule("R10")
def test_fixed_coupon(shop, user):
    """정액 쿠폰은 쿠폰 금액만큼 할인, 배송비는 할인 대상이 아니다."""
    coupon = shop.coupon(user, "FIXED", 2000)
    assert amounts(shop.order(user, {WATER: 1}, coupon)) == (5980, 3000, 2000, 6980)


@pytest.mark.rule("R9")
@pytest.mark.rule("R10")
def test_fixed_coupon_larger_than_items_is_capped(shop, user):
    """정액 쿠폰이 상품 합계보다 크면 상품 합계까지만 할인 (배송비는 그대로 결제)."""
    coupon = shop.coupon(user, "FIXED", 5000)
    assert amounts(shop.order(user, {PEN: 1}, coupon)) == (1000, 3000, 1000, 3000)


@pytest.mark.rule("R9")
@pytest.mark.parametrize("percent, expected", [(10, 598), (3, 179)])
def test_percent_coupon_floors_to_won(shop, user, percent, expected):
    """정률 쿠폰은 원 단위 버림: 5,980원의 3% = 179.4 -> 179원."""
    coupon = shop.coupon(user, "PERCENT", percent)
    assert amounts(shop.order(user, {WATER: 1}, coupon))[2] == expected


@pytest.mark.rule("R9")
@pytest.mark.parametrize("max_discount, expected", [(8899, 8899), (8900, 8900), (8901, 8900)])
def test_percent_coupon_max_discount_boundary(shop, user, max_discount, expected):
    """정률 쿠폰 최대할인 경계: 89,000원의 10% = 8,900원. 상한이 그보다 작으면 상한까지만."""
    coupon = shop.coupon(user, "PERCENT", 10, max_discount=max_discount)
    assert amounts(shop.order(user, {EARPHONE: 1}, coupon))[2] == expected


@pytest.mark.rule("R9")
def test_percent_coupon_100_makes_items_free(shop, user):
    """100% 쿠폰이면 상품 금액은 0원, 배송비만 결제."""
    coupon = shop.coupon(user, "PERCENT", 100)
    assert amounts(shop.order(user, {PEN: 2}, coupon)) == (2000, 3000, 2000, 3000)
