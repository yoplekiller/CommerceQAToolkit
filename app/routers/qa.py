"""QA 도구 API. X-QA-Token 이 맞아야 하고, QA_TOOLS_ENABLED=false 면 라우트 자체가 없는 것처럼 404."""
import os

from fastapi import APIRouter, Depends, Header, HTTPException

from app import qa_tools, services
from app.deps import get_conn
from app.schemas import AdvanceIn, BulkUsersIn, CartFillIn, CouponIssueIn, OrderGenerateIn, StockSet


def require_qa_access(x_qa_token: str | None = Header(default=None)):
    if os.getenv("QA_TOOLS_ENABLED", "true").lower() != "true":
        raise HTTPException(status_code=404, detail="Not Found")
    if x_qa_token != os.getenv("QA_TOKEN", "local-qa-token"):
        raise HTTPException(status_code=401, detail="QA 토큰이 올바르지 않습니다.")


router = APIRouter(prefix="/qa", tags=["qa-tools"], dependencies=[Depends(require_qa_access)])


@router.post("/seed")
def seed(conn=Depends(get_conn)):
    return qa_tools.seed(conn)


@router.post("/reset")
def reset(conn=Depends(get_conn)):
    qa_tools.reset(conn)
    return {"reset": True}


@router.post("/users/bulk", status_code=201)
def bulk_users(body: BulkUsersIn, conn=Depends(get_conn)):
    return qa_tools.bulk_users(conn, body.count, body.prefix)


@router.post("/coupons/issue", status_code=201)
def issue_coupon(body: CouponIssueIn, conn=Depends(get_conn)):
    return services.issue_coupon(
        conn, body.user_id, body.discount_type, body.value,
        body.min_order_amount, body.max_discount, body.expires_in_days,
    )


@router.put("/products/{product_id}/stock")
def set_stock(product_id: int, body: StockSet, conn=Depends(get_conn)):
    return qa_tools.set_stock(conn, product_id, body.stock)


@router.post("/carts/fill")
def fill_cart(body: CartFillIn, conn=Depends(get_conn)):
    return qa_tools.fill_cart(conn, body.user_id, body.count, body.seed)


@router.post("/orders/generate", status_code=201)
def generate_orders(body: OrderGenerateIn, conn=Depends(get_conn)):
    return qa_tools.generate_orders(conn, body.count, body.scenario, body.target_status, body.user_id, body.seed)


@router.post("/orders/{order_id}/advance")
def advance(order_id: int, body: AdvanceIn, conn=Depends(get_conn)):
    return services.advance_to(conn, order_id, body.target_status)
