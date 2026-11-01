"""서비스 API 테스트 공통 헬퍼.

사전조건(계정, 쿠폰, 재고, 특정 상태의 주문)은 전부 QA 도구(/qa/*)로 만든다.
테스트 본문에는 "검증하려는 동작" 호출과 assert 만 남기기 위해서다.
"""
import pytest

from tests.conftest import QA_HEADERS

# 시드 상품 ID (docs/SPEC.md 6. 시드 데이터)
EARPHONE, WATER, CABLE, SNEAKERS, TUMBLER, BATTERY, TISSUE, PEN = range(1, 9)


def assert_error(res, status_code: int, code: str) -> None:
    """에러 응답의 HTTP 상태코드와 에러 코드를 함께 검증한다."""
    assert res.status_code == status_code, res.text
    assert res.json()["error"]["code"] == code


class Shop:
    """테스트 대상 API + QA 도구 호출을 짧게 쓰기 위한 래퍼."""

    def __init__(self, client):
        self.client = client

    # ---------- QA 도구로 사전조건 만들기 ----------

    def _qa(self, method: str, path: str, body=None):
        res = self.client.request(method, f"/qa{path}", json=body, headers=QA_HEADERS)
        assert res.status_code < 400, res.text
        return res.json()

    def new_user(self) -> int:
        return self._qa("POST", "/users/bulk", {"count": 1})[0]["id"]

    def new_product(self, price: int, stock: int = 100, max_per_order: int | None = None, is_active: bool = True) -> int:
        return self._qa("POST", "/products", {
            "name": f"테스트상품-{price}", "price": price, "stock": stock,
            "max_per_order": max_per_order, "is_active": is_active,
        })["id"]

    def set_stock(self, product_id: int, stock: int) -> None:
        self._qa("PUT", f"/products/{product_id}/stock", {"stock": stock})

    def coupon(self, user_id: int, discount_type: str = "FIXED", value: int = 1000, **kwargs) -> int:
        return self._qa("POST", "/coupons/issue", {
            "user_id": user_id, "discount_type": discount_type, "value": value, **kwargs,
        })["id"]

    def order_in(self, status: str, **kwargs) -> dict:
        """target 상태까지 진행된 주문 1건 (QA 도구 주문 자동생성)."""
        return self._qa("POST", "/orders/generate", {"count": 1, "target_status": status, **kwargs})[0]

    def fill_cart(self, user_id: int, count: int) -> list[dict]:
        return self._qa("POST", "/carts/fill", {"user_id": user_id, "count": count, "seed": 1})

    # ---------- 테스트 대상 서비스 API ----------

    def stock(self, product_id: int) -> int:
        return self.client.get(f"/products/{product_id}").json()["stock"]

    def order(self, user_id: int, items: dict[int, int] | list[tuple[int, int]], coupon_id: int | None = None):
        """items: {상품ID: 수량} 또는 [(상품ID, 수량), ...] (같은 상품 여러 줄 테스트용)."""
        pairs = items.items() if isinstance(items, dict) else items
        return self.client.post("/orders", json={
            "user_id": user_id,
            "items": [{"product_id": pid, "quantity": q} for pid, q in pairs],
            "coupon_id": coupon_id,
        })

    def cart_add(self, user_id: int, product_id: int, quantity: int):
        return self.client.post(f"/users/{user_id}/cart", json={"product_id": product_id, "quantity": quantity})

    def cart(self, user_id: int) -> list[dict]:
        return self.client.get(f"/users/{user_id}/cart").json()

    def coupons(self, user_id: int) -> list[dict]:
        return self.client.get(f"/users/{user_id}/coupons").json()


@pytest.fixture
def shop(client):
    return Shop(client)


@pytest.fixture
def user(shop):
    """테스트마다 새 테스트 계정 ID."""
    return shop.new_user()
