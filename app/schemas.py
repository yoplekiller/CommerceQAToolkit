from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["PENDING", "ACCEPTED", "COOKING", "DELIVERING", "DELIVERED", "CANCELED"]


class UserCreate(BaseModel):
    email: str
    name: str


class OrderItemIn(BaseModel):
    menu_id: int
    # 수량 범위는 도메인 규칙(INVALID_QUANTITY)으로 검사해서 에러 코드를 통일한다
    quantity: int


class OrderCreate(BaseModel):
    user_id: int
    store_id: int
    items: list[OrderItemIn]
    coupon_id: int | None = None


class StatusChange(BaseModel):
    status: str
    actor: Literal["store", "rider"] = "store"
    reason: str | None = None


class CancelRequest(BaseModel):
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


class OrderGenerateIn(BaseModel):
    count: int = Field(1, ge=1, le=100)
    scenario: Literal["basic", "multi_item", "coupon_fixed", "coupon_percent"] = "basic"
    target_status: Status = "PENDING"
    user_id: int | None = None
    store_id: int | None = None
    seed: int | None = None


class AdvanceIn(BaseModel):
    target_status: Status
