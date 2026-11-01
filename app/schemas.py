from typing import Literal

from pydantic import BaseModel, Field

Status = Literal[
    "PAID", "PREPARING", "SHIPPING", "DELIVERED", "CONFIRMED", "CANCELED", "RETURN_REQUESTED", "RETURNED",
]


class UserCreate(BaseModel):
    """회원 가입 요청."""
    email: str
    name: str


class LoginIn(BaseModel):
    """데모용 간이 로그인 요청 (이메일만)."""
    email: str


class CartAdd(BaseModel):
    """장바구니 담기 요청."""
    product_id: int
    # 수량 범위는 도메인 규칙(INVALID_QUANTITY)으로 검사해서 에러 코드를 통일한다
    quantity: int = 1


class OrderItemIn(BaseModel):
    """주문 상품 1줄."""
    product_id: int
    quantity: int


class OrderCreate(BaseModel):
    """바로 주문 요청."""
    user_id: int
    items: list[OrderItemIn]
    coupon_id: int | None = None


class QuoteIn(BaseModel):
    """주문서 금액 계산 요청. items 를 안 주면 장바구니 기준."""
    user_id: int
    items: list[OrderItemIn] | None = None
    coupon_id: int | None = None


class CheckoutIn(BaseModel):
    """장바구니 주문 요청."""
    user_id: int
    coupon_id: int | None = None


class StatusChange(BaseModel):
    """판매자/택배사 상태 변경 요청."""
    status: str
    actor: Literal["seller", "carrier"] = "seller"
    reason: str | None = None


class ReasonIn(BaseModel):
    """취소/반품 사유 (선택)."""
    reason: str | None = None


# ---------- QA 도구 ----------

class BulkUsersIn(BaseModel):
    """테스트 계정 일괄 생성 요청 (최대 100개)."""
    count: int = Field(1, ge=1, le=100)
    prefix: str = "qa"


class CouponIssueIn(BaseModel):
    """쿠폰 발급 요청."""
    user_id: int
    discount_type: Literal["FIXED", "PERCENT"]
    value: int
    min_order_amount: int = 0
    max_discount: int | None = None
    # 음수로 주면 이미 만료된 쿠폰을 만든다 (만료 쿠폰 테스트용)
    expires_in_days: int = 30


class ProductCreate(BaseModel):
    """테스트 상품 생성 요청."""
    name: str
    price: int
    stock: int
    max_per_order: int | None = None
    is_active: bool = True


class StockSet(BaseModel):
    """재고 설정 요청."""
    stock: int


class CartFillIn(BaseModel):
    """장바구니 자동 담기 요청."""
    user_id: int
    count: int = Field(3, ge=1, le=20)
    seed: int | None = None


class OrderGenerateIn(BaseModel):
    """주문 자동생성 요청."""
    count: int = Field(1, ge=1, le=100)
    scenario: Literal[
        "basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping",
    ] = "basic"
    target_status: Status = "PAID"
    user_id: int | None = None
    seed: int | None = None


class AdvanceIn(BaseModel):
    """주문 상태 자동완료 요청."""
    target_status: Status
