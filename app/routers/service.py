"""테스트 대상 서비스 API (손님/가게/라이더가 쓰는 API)."""
from fastapi import APIRouter, Depends

from app import services
from app.deps import get_conn
from app.schemas import CancelRequest, OrderCreate, StatusChange, UserCreate

router = APIRouter()


@router.get("/stores")
def list_stores(conn=Depends(get_conn)):
    return services.list_stores(conn)


@router.get("/stores/{store_id}/menus")
def list_menus(store_id: int, conn=Depends(get_conn)):
    return services.list_menus(conn, store_id)


@router.post("/users", status_code=201)
def create_user(body: UserCreate, conn=Depends(get_conn)):
    return services.create_user(conn, body.email, body.name)


@router.get("/users/{user_id}/coupons")
def list_coupons(user_id: int, conn=Depends(get_conn)):
    return services.list_coupons(conn, user_id)


@router.post("/orders", status_code=201)
def create_order(body: OrderCreate, conn=Depends(get_conn)):
    items = [item.model_dump() for item in body.items]
    return services.create_order(conn, body.user_id, body.store_id, items, body.coupon_id)


@router.get("/orders/{order_id}")
def get_order(order_id: int, conn=Depends(get_conn)):
    return services.get_order(conn, order_id)


@router.post("/orders/{order_id}/status")
def change_status(order_id: int, body: StatusChange, conn=Depends(get_conn)):
    return services.change_status(conn, order_id, body.status, body.actor, body.reason)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: CancelRequest | None = None, conn=Depends(get_conn)):
    return services.cancel_by_customer(conn, order_id, body.reason if body else None)
