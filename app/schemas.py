from typing import Literal

from pydantic import BaseModel, Field

Status = Literal[
    "PAID", "PREPARING", "SHIPPING", "DELIVERED", "CONFIRMED", "CANCELED", "RETURN_REQUESTED", "RETURNED",
]


class UserCreate(BaseModel):
    email: str
    name: str


class CartAdd(BaseModel):
    product_id: int
    # 수량 범위는 도메인 규칙(INVALID_QUANTITY)으로 검사해서 에러 코드를 통일한다
    quantity: int = 1


class OrderItemIn(BaseModel):
    product_id: int
    quantity: int


class OrderCreate(BaseModel):
    user_id: int
    items: list[OrderItemIn]
    coupon_id: int | None = None


class CheckoutIn(BaseModel):
    user_id: int
    coupon_id: int | None = None


class StatusChange(BaseModel):
    status: str
    actor: Literal["seller", "carrier"] = "seller"
    reason: str | None = None


class ReasonIn(BaseModel):
    reason: str | None = None


# ---------- QA 도구 ----------

class BulkUsersIn(BaseModel):
    count: int = Field(1, ge=1, le=100)
    prefix: str = "qa"


class CouponIssueIn(BaseModel):
    user_id: int
    discount_type: Literal["FIXED", "PERCENT"]
    value: int
    min_order_amount: int = 0
    max_discount: int | None = None
    # 음수로 주면 이미 만료된 쿠폰을 만든다 (만료 쿠폰 테스트용)
    expires_in_days: int = 30


class StockSet(BaseModel):
    stock: int


class CartFillIn(BaseModel):
    user_id: int
    count: int = Field(3, ge=1, le=20)
    seed: int | None = None


class OrderGenerateIn(BaseModel):
    count: int = Field(1, ge=1, le=100)
    scenario: Literal[
        "basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping",
    ] = "basic"
    target_status: Status = "PAID"
    user_id: int | None = None
    seed: int | None = None


class AdvanceIn(BaseModel):
    target_status: Status
