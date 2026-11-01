"""QA 도구 API. X-QA-Token 이 맞아야 하고, QA_TOOLS_ENABLED=false 면 라우트 자체가 없는 것처럼 404."""
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import qa_tools, services
from app.deps import get_conn
from app.schemas import AdvanceIn, BulkUsersIn, CartFillIn, CouponIssueIn, OrderGenerateIn, ProductCreate, StockSet


def require_qa_access(x_qa_token: str | None = Header(default=None)):
    """QA 도구 접근 검사. 기능이 꺼져 있으면 404, 토큰이 틀리면 401."""
    if os.getenv("QA_TOOLS_ENABLED", "true").lower() != "true":
        raise HTTPException(status_code=404, detail="Not Found")
    if x_qa_token != os.getenv("QA_TOKEN", "local-qa-token"):
        raise HTTPException(status_code=401, detail="QA 토큰이 올바르지 않습니다.")


router = APIRouter(prefix="/qa", tags=["qa-tools"], dependencies=[Depends(require_qa_access)])


@router.post("/seed")
def seed(conn=Depends(get_conn)):
    """초기화 후 기본 상품 8종 생성"""
    return qa_tools.seed(conn)


@router.post("/reset")
def reset(conn=Depends(get_conn)):
    """전체 데이터 초기화"""
    qa_tools.reset(conn)
    return {"reset": True}


@router.post("/users/bulk", status_code=201)
def bulk_users(body: BulkUsersIn, conn=Depends(get_conn)):
    """테스트 계정 일괄 생성"""
    return qa_tools.bulk_users(conn, body.count, body.prefix)


@router.post("/coupons/issue", status_code=201)
def issue_coupon(body: CouponIssueIn, conn=Depends(get_conn)):
    """쿠폰 발급 (expires_in_days 가 음수면 만료 쿠폰)"""
    return services.issue_coupon(
        conn, body.user_id, body.discount_type, body.value,
        body.min_order_amount, body.max_discount, body.expires_in_days,
    )


@router.post("/products", status_code=201)
def create_product(body: ProductCreate, conn=Depends(get_conn)):
    """테스트 상품 생성 (경계값 가격/재고 상품 만들기)"""
    return qa_tools.create_product(conn, body.name, body.price, body.stock, body.max_per_order, body.is_active)


@router.put("/products/{product_id}/stock")
def set_stock(product_id: int, body: StockSet, conn=Depends(get_conn)):
    """상품 재고 설정 (0 이면 품절)"""
    return qa_tools.set_stock(conn, product_id, body.stock)


@router.post("/carts/fill")
def fill_cart(body: CartFillIn, conn=Depends(get_conn)):
    """장바구니 자동 담기"""
    return qa_tools.fill_cart(conn, body.user_id, body.count, body.seed)


@router.post("/orders/generate", status_code=201)
def generate_orders(body: OrderGenerateIn, conn=Depends(get_conn)):
    """주문 자동생성 + 목표 상태까지 자동 진행"""
    return qa_tools.generate_orders(conn, body.count, body.scenario, body.target_status, body.user_id, body.seed)


@router.get("/orders")
def list_orders(status: str | None = None, limit: int = Query(50, ge=1, le=200), conn=Depends(get_conn)):
    """최근 주문 목록 (최신순, status 로 필터)"""
    return qa_tools.list_orders(conn, status, limit)


@router.post("/orders/{order_id}/advance")
def advance(order_id: int, body: AdvanceIn, conn=Depends(get_conn)):
    """주문 상태 자동완료 (허용된 전이를 한 단계씩)"""
    return services.advance_to(conn, order_id, body.target_status)
