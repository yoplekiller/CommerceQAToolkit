"""테스트 대상 서비스 API (고객/판매자/택배사가 쓰는 API)."""
from fastapi import APIRouter, Depends

from app import services
from app.deps import get_conn
from app.schemas import CartAdd, CheckoutIn, OrderCreate, ReasonIn, StatusChange, UserCreate

router = APIRouter()


@router.get("/products")
def list_products(conn=Depends(get_conn)):
    return services.list_products(conn)


@router.get("/products/{product_id}")
def get_product(product_id: int, conn=Depends(get_conn)):
    return services.get_product(conn, product_id)


@router.post("/users", status_code=201)
def create_user(body: UserCreate, conn=Depends(get_conn)):
    return services.create_user(conn, body.email, body.name)


@router.get("/users/{user_id}/coupons")
def list_coupons(user_id: int, conn=Depends(get_conn)):
    return services.list_coupons(conn, user_id)


@router.get("/users/{user_id}/cart")
def get_cart(user_id: int, conn=Depends(get_conn)):
    return services.get_cart(conn, user_id)


@router.post("/users/{user_id}/cart")
def add_to_cart(user_id: int, body: CartAdd, conn=Depends(get_conn)):
    return services.add_to_cart(conn, user_id, body.product_id, body.quantity)


@router.delete("/users/{user_id}/cart/{product_id}")
def remove_from_cart(user_id: int, product_id: int, conn=Depends(get_conn)):
    return services.remove_from_cart(conn, user_id, product_id)


@router.post("/orders", status_code=201)
def create_order(body: OrderCreate, conn=Depends(get_conn)):
    items = [item.model_dump() for item in body.items]
    return services.create_order(conn, body.user_id, items, body.coupon_id)


@router.post("/orders/checkout", status_code=201)
def checkout(body: CheckoutIn, conn=Depends(get_conn)):
    return services.checkout_cart(conn, body.user_id, body.coupon_id)


@router.get("/orders/{order_id}")
def get_order(order_id: int, conn=Depends(get_conn)):
    return services.get_order(conn, order_id)


@router.post("/orders/{order_id}/status")
def change_status(order_id: int, body: StatusChange, conn=Depends(get_conn)):
    return services.change_status(conn, order_id, body.status, body.actor, body.reason)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: ReasonIn | None = None, conn=Depends(get_conn)):
    return services.cancel_by_customer(conn, order_id, body.reason if body else None)


@router.post("/orders/{order_id}/return")
def request_return(order_id: int, body: ReasonIn | None = None, conn=Depends(get_conn)):
    return services.request_return(conn, order_id, body.reason if body else None)


@router.post("/orders/{order_id}/confirm")
def confirm_purchase(order_id: int, conn=Depends(get_conn)):
    return services.confirm_purchase(conn, order_id)
