"""판매자 상품 관리 API (R16): 등록 · 수정 입력 검사, 수정이 주문/쇼핑에 미치는 영향."""
import pytest

from tests.api.conftest import PEN, SNEAKERS, WATER, assert_error


def register(client, **overrides):
    body = {"name": "테스트 상품", "price": 10000, "stock": 10, **overrides}
    return client.post("/admin/products", json=body)


@pytest.mark.rule("R16")
def test_register_product_then_orderable(client, shop, user):
    """등록한 상품은 상품 목록에 나오고 바로 주문할 수 있다."""
    product = register(client, name="  머그컵  ", price=12000, stock=3, max_per_order=2).json()
    assert product["name"] == "머그컵"  # 앞뒤 공백 제거
    assert product["max_per_order"] == 2
    assert shop.order(user, {product["id"]: 2}).status_code == 201
    assert shop.stock(product["id"]) == 1


@pytest.mark.rule("R16")
@pytest.mark.parametrize("field, value, ok", [
    ("price", 0, False), ("price", 1, True), ("price", 10_000_000, True), ("price", 10_000_001, False),
    ("stock", -1, False), ("stock", 0, True), ("stock", 99_999, True), ("stock", 100_000, False),
    ("max_per_order", 0, False), ("max_per_order", 1, True), ("max_per_order", 99, True), ("max_per_order", 100, False),
    ("name", "", False), ("name", "   ", False), ("name", "가" * 50, True), ("name", "가" * 51, False),
])
def test_register_boundaries(client, field, value, ok):
    """등록 입력 경계값: 가격 1~1천만원, 재고 0~99,999, 구매 제한 1~99, 상품명 1~50자(공백만은 불가)."""
    res = register(client, **{field: value})
    if ok:
        assert res.status_code == 201, res.text
    else:
        assert_error(res, 400, "INVALID_PRODUCT")


@pytest.mark.rule("R16")
def test_update_only_sent_fields(client):
    """보낸 항목만 바뀌고 나머지는 그대로다."""
    before = client.get(f"/products/{WATER}").json()
    after = client.patch(f"/admin/products/{WATER}", json={"stock": 7}).json()
    assert after["stock"] == 7
    assert {k: after[k] for k in ("name", "price", "max_per_order", "is_active")} == \
           {k: before[k] for k in ("name", "price", "max_per_order", "is_active")}


@pytest.mark.rule("R16")
def test_update_null_limit_removes_purchase_limit(client, shop, user):
    """구매 제한을 null 로 보내면 해제돼서 한정판 2개 주문이 된다."""
    client.patch(f"/admin/products/{SNEAKERS}", json={"max_per_order": None})
    assert shop.order(user, {SNEAKERS: 2}).status_code == 201


@pytest.mark.rule("R16")
def test_update_invalid_value_changes_nothing(client):
    """잘못된 값(재고 -1)이 섞인 수정은 통째로 거부되고 다른 항목도 안 바뀐다."""
    before = client.get(f"/products/{WATER}").json()
    assert_error(client.patch(f"/admin/products/{WATER}", json={"price": 9999, "stock": -1}), 400, "INVALID_PRODUCT")
    assert client.get(f"/products/{WATER}").json() == before


@pytest.mark.rule("R16")
def test_price_change_does_not_affect_existing_order(client, shop, user):
    """가격을 올려도 이미 들어온 주문 금액은 그대로, 새 주문만 새 가격이다."""
    old = shop.order(user, {PEN: 1}).json()
    client.patch(f"/admin/products/{PEN}", json={"price": 5000})
    assert client.get(f"/orders/{old['id']}").json()["items_amount"] == 1000
    assert shop.order(user, {PEN: 1}).json()["items_amount"] == 5000


@pytest.mark.rule("R1")
@pytest.mark.rule("R16")
def test_deactivate_blocks_order_and_reactivate_allows(client, shop, user):
    """판매중지하면 PRODUCT_INACTIVE, 다시 판매하면 주문된다."""
    client.patch(f"/admin/products/{PEN}", json={"is_active": False})
    assert_error(shop.order(user, {PEN: 1}), 400, "PRODUCT_INACTIVE")
    client.patch(f"/admin/products/{PEN}", json={"is_active": True})
    assert shop.order(user, {PEN: 1}).status_code == 201


def test_update_missing_product(client):
    """없는 상품 수정은 404."""
    assert_error(client.patch("/admin/products/999", json={"stock": 1}), 404, "PRODUCT_NOT_FOUND")
