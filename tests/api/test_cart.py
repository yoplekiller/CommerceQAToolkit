"""장바구니 담기/합산/주문 (R15)."""
import pytest

from tests.api.conftest import BATTERY, CABLE, PEN, SNEAKERS, TUMBLER, WATER, assert_error

pytestmark = pytest.mark.rule("R15")


def test_add_same_product_merges_quantity(shop, user):
    """같은 상품을 다시 담으면 수량이 합쳐진다: 1 + 2 = 3."""
    assert shop.cart_add(user, PEN, 1).status_code == 200
    cart = shop.cart_add(user, PEN, 2).json()
    assert [(c["product_id"], c["quantity"]) for c in cart] == [(PEN, 3)]


def test_merged_quantity_checked_against_purchase_limit(shop, user):
    """1개 제한 상품을 1개 담은 뒤 1개 더 담으면 400 PURCHASE_LIMIT_EXCEEDED, 장바구니는 1개 그대로."""
    shop.cart_add(user, SNEAKERS, 1)
    assert_error(shop.cart_add(user, SNEAKERS, 1), 400, "PURCHASE_LIMIT_EXCEEDED")
    assert shop.cart(user)[0]["quantity"] == 1


def test_merged_quantity_checked_against_stock(shop, user):
    """재고 3개 상품을 2개 담고 2개 더 담으면 409 INSUFFICIENT_STOCK."""
    shop.set_stock(CABLE, 3)
    shop.cart_add(user, CABLE, 2)
    assert_error(shop.cart_add(user, CABLE, 2), 409, "INSUFFICIENT_STOCK")


@pytest.mark.parametrize("product, status_code, code", [
    (BATTERY, 400, "PRODUCT_INACTIVE"),
    (TUMBLER, 409, "SOLD_OUT"),
    (999, 404, "PRODUCT_NOT_FOUND"),
])
def test_unorderable_product_cannot_be_added(shop, user, product, status_code, code):
    """판매중지/품절/없는 상품은 장바구니에도 못 담는다."""
    assert_error(shop.cart_add(user, product, 1), status_code, code)


@pytest.mark.parametrize("quantity", [0, -1, 100])
def test_add_invalid_quantity_rejected_even_if_already_in_cart(shop, user, quantity):
    """이미 담긴 상품이어도 추가 수량 자체가 1~99 가 아니면 400 INVALID_QUANTITY, 장바구니는 그대로."""
    shop.cart_add(user, PEN, 3)
    assert_error(shop.cart_add(user, PEN, quantity), 400, "INVALID_QUANTITY")
    assert shop.cart(user)[0]["quantity"] == 3


def test_remove_from_cart(shop, user, client):
    """장바구니에서 상품을 빼면 나머지만 남는다."""
    shop.cart_add(user, PEN, 1)
    shop.cart_add(user, WATER, 1)
    cart = client.delete(f"/users/{user}/cart/{PEN}").json()
    assert [c["product_id"] for c in cart] == [WATER]


def test_checkout_orders_whole_cart_and_empties_it(shop, user, client):
    """장바구니 주문: 담긴 상품/수량 그대로 주문되고 장바구니가 비워진다 (QA 도구로 장바구니 준비)."""
    cart = shop.fill_cart(user, 3)
    res = client.post("/orders/checkout", json={"user_id": user})
    assert res.status_code == 201
    ordered = {(i["product_id"], i["quantity"]) for i in res.json()["items"]}
    assert ordered == {(c["product_id"], c["quantity"]) for c in cart}
    assert shop.cart(user) == []


def test_checkout_empty_cart(user, client):
    """빈 장바구니 주문은 400 EMPTY_CART."""
    assert_error(client.post("/orders/checkout", json={"user_id": user}), 400, "EMPTY_CART")


def test_failed_checkout_keeps_cart(shop, user, client):
    """담은 뒤 품절된 상품이 있으면 주문은 409 SOLD_OUT 으로 실패하고 장바구니는 그대로 남는다."""
    shop.cart_add(user, PEN, 1)
    shop.cart_add(user, CABLE, 1)
    shop.set_stock(CABLE, 0)
    assert_error(client.post("/orders/checkout", json={"user_id": user}), 409, "SOLD_OUT")
    assert len(shop.cart(user)) == 2
