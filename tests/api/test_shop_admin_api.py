"""쇼핑몰·판매자 어드민 화면이 쓰는 API: 주문서 금액 계산, 간이 로그인, 내 주문내역, 판매자 주문 목록."""
import pytest

from tests.api.conftest import PEN, SNEAKERS, TISSUE, WATER, assert_error


def quote(client, user_id, items=None, coupon_id=None):
    body = {"user_id": user_id, "coupon_id": coupon_id}
    if items is not None:
        body["items"] = [{"product_id": pid, "quantity": q} for pid, q in items.items()]
    return client.post("/orders/quote", json=body)


# ---------- 주문서 금액 계산 (/orders/quote) ----------

@pytest.mark.rule("R7")
@pytest.mark.rule("R10")
def test_quote_matches_created_order_amounts(client, shop, user):
    """주문서 계산 금액이 실제로 주문했을 때 금액과 같다 (화면 표시 금액 = 결제 금액)."""
    coupon_id = shop.coupon(user, "PERCENT", 10, max_discount=5000)
    preview = quote(client, user, {WATER: 2, PEN: 3}, coupon_id).json()
    order = shop.order(user, {WATER: 2, PEN: 3}, coupon_id).json()
    for key in ("items_amount", "shipping_fee", "discount_amount", "total_amount"):
        assert preview[key] == order[key], key


@pytest.mark.rule("R7")
@pytest.mark.parametrize("quantity, remaining", [(1, 13820), (3, 1860), (4, 0)])
def test_quote_free_shipping_remaining(client, user, quantity, remaining):
    """무료배송까지 남은 금액: 생수(5,980원) 1개 13,820원 / 3개 1,860원 / 4개(23,920원) 0원."""
    assert quote(client, user, {WATER: quantity}).json()["free_shipping_remaining"] == remaining


def test_quote_does_not_change_stock_or_coupon(client, shop, user):
    """주문서 계산은 저장하지 않는다: 재고와 쿠폰 사용 여부가 그대로다."""
    coupon_id = shop.coupon(user, value=1000)
    before = shop.stock(TISSUE)
    assert quote(client, user, {TISSUE: 2}, coupon_id).status_code == 200
    assert shop.stock(TISSUE) == before
    assert shop.coupons(user)[0]["used_order_id"] is None


def test_quote_uses_cart_when_items_omitted(client, shop, user):
    """items 를 안 주면 장바구니 기준으로 계산한다."""
    shop.cart_add(user, TISSUE, 1)
    shop.cart_add(user, PEN, 2)
    data = quote(client, user).json()
    assert data["items_amount"] == 19800 + 2000
    assert {i["product_id"] for i in data["items"]} == {TISSUE, PEN}


@pytest.mark.rule("R4")
def test_quote_rejects_same_as_order(client, user):
    """주문서 계산도 주문과 같은 검사를 한다: 한정판 2개는 PURCHASE_LIMIT_EXCEEDED."""
    assert_error(quote(client, user, {SNEAKERS: 2}), 400, "PURCHASE_LIMIT_EXCEEDED")


def test_quote_empty_cart(client, user):
    """장바구니가 비어 있으면 EMPTY_ORDER."""
    assert_error(quote(client, user), 400, "EMPTY_ORDER")


# ---------- 간이 로그인 / 회원 ----------

def test_login_by_email(client):
    """가입한 이메일로 로그인하면 회원 정보가 오고, 없는 이메일은 USER_NOT_FOUND."""
    user = client.post("/users", json={"email": "buyer@test.local", "name": "buyer"}).json()
    assert client.post("/users/login", json={"email": "buyer@test.local"}).json()["id"] == user["id"]
    assert_error(client.post("/users/login", json={"email": "nobody@test.local"}), 404, "USER_NOT_FOUND")


def test_get_user(client, user):
    """회원 정보 조회, 없는 회원은 404."""
    assert client.get(f"/users/{user}").json()["id"] == user
    assert_error(client.get("/users/9999"), 404, "USER_NOT_FOUND")


# ---------- 내 주문내역 ----------

def test_user_orders_only_mine_latest_first(client, shop, user):
    """내 주문내역은 본인 주문만, 최신순이다."""
    other = shop.new_user()
    first = shop.order(user, {PEN: 1}).json()["id"]
    shop.order(other, {PEN: 1})
    second = shop.order(user, {PEN: 1}).json()["id"]
    assert [o["id"] for o in client.get(f"/users/{user}/orders").json()] == [second, first]


# ---------- 판매자 어드민 주문 목록 ----------

def test_admin_orders_filter_and_counts(client, shop):
    """판매자 주문 목록: status 필터 + 상태별 건수(모든 상태 키 포함)."""
    shop.order_in("PAID")
    shop.order_in("PAID")
    shop.order_in("SHIPPING")
    data = client.get("/admin/orders?status=PAID").json()
    assert [o["status"] for o in data["orders"]] == ["PAID", "PAID"]
    assert data["counts"]["PAID"] == 2
    assert data["counts"]["SHIPPING"] == 1
    assert data["counts"]["RETURNED"] == 0


def test_admin_orders_invalid_status(client):
    """존재하지 않는 상태로 거르면 INVALID_STATUS."""
    assert_error(client.get("/admin/orders?status=LOST"), 400, "INVALID_STATUS")
