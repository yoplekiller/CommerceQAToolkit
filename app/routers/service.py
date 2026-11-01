"""테스트 대상 서비스 API (고객/판매자/택배사가 쓰는 API)."""
from fastapi import APIRouter, Depends, Query

from app import services
from app.deps import get_conn
from app.schemas import (
    CartAdd, CheckoutIn, LoginIn, OrderCreate, ProductRegister, ProductUpdate, QuoteIn, ReasonIn, StatusChange, UserCreate,
)

router = APIRouter()


@router.get("/products")
def list_products(conn=Depends(get_conn)):
    """상품 목록"""
    return services.list_products(conn)


@router.get("/products/{product_id}")
def get_product(product_id: int, conn=Depends(get_conn)):
    """상품 상세"""
    return services.get_product(conn, product_id)


@router.post("/users", status_code=201)
def create_user(body: UserCreate, conn=Depends(get_conn)):
    """회원 가입"""
    return services.create_user(conn, body.email, body.name)


@router.post("/users/login")
def login(body: LoginIn, conn=Depends(get_conn)):
    """데모용 간이 로그인 (이메일만, 비밀번호 없음)"""
    return services.login(conn, body.email)


@router.get("/users/{user_id}")
def get_user(user_id: int, conn=Depends(get_conn)):
    """회원 정보"""
    return services.get_user(conn, user_id)


@router.get("/users/{user_id}/orders")
def list_user_orders(user_id: int, conn=Depends(get_conn)):
    """내 주문내역 (최신순)"""
    return services.list_user_orders(conn, user_id)


@router.get("/users/{user_id}/coupons")
def list_coupons(user_id: int, conn=Depends(get_conn)):
    """보유 쿠폰 목록"""
    return services.list_coupons(conn, user_id)


@router.get("/users/{user_id}/cart")
def get_cart(user_id: int, conn=Depends(get_conn)):
    """장바구니 조회"""
    return services.get_cart(conn, user_id)


@router.post("/users/{user_id}/cart")
def add_to_cart(user_id: int, body: CartAdd, conn=Depends(get_conn)):
    """장바구니 담기 (같은 상품이면 수량 합산)"""
    return services.add_to_cart(conn, user_id, body.product_id, body.quantity)


@router.delete("/users/{user_id}/cart/{product_id}")
def remove_from_cart(user_id: int, product_id: int, conn=Depends(get_conn)):
    """장바구니에서 상품 삭제"""
    return services.remove_from_cart(conn, user_id, product_id)


@router.post("/orders", status_code=201)
def create_order(body: OrderCreate, conn=Depends(get_conn)):
    """바로 주문 (주문 생성 = 결제완료)"""
    items = [item.model_dump() for item in body.items]
    return services.create_order(conn, body.user_id, items, body.coupon_id)


@router.post("/orders/quote")
def quote_order(body: QuoteIn, conn=Depends(get_conn)):
    """주문서 금액 미리보기 (저장 안 함). items 를 안 주면 장바구니 기준"""
    if body.items is None:
        items = [{"product_id": c["product_id"], "quantity": c["quantity"]} for c in services.get_cart(conn, body.user_id)]
    else:
        items = [item.model_dump() for item in body.items]
    return services.quote_order(conn, body.user_id, items, body.coupon_id)


@router.post("/orders/checkout", status_code=201)
def checkout(body: CheckoutIn, conn=Depends(get_conn)):
    """장바구니 전체 주문 후 장바구니 비우기"""
    return services.checkout_cart(conn, body.user_id, body.coupon_id)


@router.get("/orders/{order_id}")
def get_order(order_id: int, conn=Depends(get_conn)):
    """주문 상세 (주문 상품 + 상태 이력)"""
    return services.get_order(conn, order_id)


@router.post("/orders/{order_id}/status")
def change_status(order_id: int, body: StatusChange, conn=Depends(get_conn)):
    """판매자/택배사 상태 변경 (한 단계씩, 구매확정·반품요청은 불가)"""
    return services.change_status_by_staff(conn, order_id, body.status, body.actor, body.reason)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: ReasonIn | None = None, conn=Depends(get_conn)):
    """고객 취소 (배송 시작 전까지)"""
    return services.cancel_by_customer(conn, order_id, body.reason if body else None)


@router.post("/orders/{order_id}/return")
def request_return(order_id: int, body: ReasonIn | None = None, conn=Depends(get_conn)):
    """고객 반품 요청 (배송완료 상태에서만)"""
    return services.request_return(conn, order_id, body.reason if body else None)


@router.post("/orders/{order_id}/confirm")
def confirm_purchase(order_id: int, conn=Depends(get_conn)):
    """고객 구매확정 (배송완료 상태에서만)"""
    return services.confirm_purchase(conn, order_id)


@router.get("/admin/orders")
def admin_list_orders(status: str | None = None, limit: int = Query(100, ge=1, le=200), conn=Depends(get_conn)):
    """판매자 어드민 주문 목록 (최신순, status 로 필터) + 상태별 건수"""
    return {"orders": services.list_orders(conn, status, limit), "counts": services.count_orders_by_status(conn)}


@router.post("/admin/products", status_code=201)
def register_product(body: ProductRegister, conn=Depends(get_conn)):
    """판매자 상품 등록"""
    return services.register_product(conn, body.name, body.price, body.stock, body.max_per_order, body.is_active)


@router.patch("/admin/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate, conn=Depends(get_conn)):
    """판매자 상품 수정 (가격 · 재고 · 구매 제한 · 판매중지/재개). 보낸 항목만 바뀐다"""
    changes = body.model_dump(exclude_unset=True)
    # name/price/stock/is_active 는 null 로 보내도 '안 바꿈'으로 처리 (null 이 의미 있는 건 구매 제한 해제뿐)
    changes = {k: v for k, v in changes.items() if v is not None or k == "max_per_order"}
    return services.update_product(conn, product_id, changes)
