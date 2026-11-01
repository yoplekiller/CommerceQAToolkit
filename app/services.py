"""배달 주문 도메인 로직. API 라우터와 QA 도구가 모두 이 함수들을 거친다."""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

PENDING = "PENDING"
ACCEPTED = "ACCEPTED"
COOKING = "COOKING"
DELIVERING = "DELIVERING"
DELIVERED = "DELIVERED"
CANCELED = "CANCELED"

# 정상 흐름 순서 (QA 도구 자동완료가 이 순서대로 한 단계씩 밟는다)
FLOW = [PENDING, ACCEPTED, COOKING, DELIVERING, DELIVERED]
STATUSES = FLOW + [CANCELED]

ALLOWED_TRANSITIONS = {
    PENDING: {ACCEPTED, CANCELED},
    ACCEPTED: {COOKING, CANCELED},
    COOKING: {DELIVERING, CANCELED},
    DELIVERING: {DELIVERED},
    DELIVERED: set(),
    CANCELED: set(),
}

MAX_QUANTITY = 99


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


# ---------- 조회 ----------

def list_stores(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM stores ORDER BY id")]


def get_store(conn: sqlite3.Connection, store_id: int) -> dict:
    row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
    if not row:
        raise DomainError("STORE_NOT_FOUND", f"가게를 찾을 수 없습니다: {store_id}", 404)
    return dict(row)


def list_menus(conn: sqlite3.Connection, store_id: int) -> list[dict]:
    get_store(conn, store_id)
    return [dict(r) for r in conn.execute("SELECT * FROM menus WHERE store_id = ? ORDER BY id", (store_id,))]


def get_user(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise DomainError("USER_NOT_FOUND", f"회원을 찾을 수 없습니다: {user_id}", 404)
    return dict(row)


def list_coupons(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    get_user(conn, user_id)
    return [dict(r) for r in conn.execute("SELECT * FROM coupons WHERE user_id = ? ORDER BY id", (user_id,))]


def get_order(conn: sqlite3.Connection, order_id: int) -> dict:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise DomainError("ORDER_NOT_FOUND", f"주문을 찾을 수 없습니다: {order_id}", 404)
    order = dict(row)
    order["items"] = [
        dict(r) for r in conn.execute(
            "SELECT menu_id, menu_name, unit_price, quantity FROM order_items WHERE order_id = ? ORDER BY id",
            (order_id,),
        )
    ]
    order["history"] = [
        dict(r) for r in conn.execute(
            "SELECT from_status, to_status, actor, changed_at FROM order_status_history WHERE order_id = ? ORDER BY id",
            (order_id,),
        )
    ]
    return order


# ---------- 회원 / 쿠폰 ----------

def create_user(conn: sqlite3.Connection, email: str, name: str, is_test: bool = False) -> dict:
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise DomainError("EMAIL_DUPLICATED", f"이미 가입된 이메일입니다: {email}", 409)
    with conn:
        cur = conn.execute(
            "INSERT INTO users (email, name, is_test, created_at) VALUES (?, ?, ?, ?)",
            (email, name, int(is_test), _ts()),
        )
    return get_user(conn, cur.lastrowid)


def issue_coupon(
    conn: sqlite3.Connection,
    user_id: int,
    discount_type: str,
    value: int,
    min_order_amount: int = 0,
    max_discount: int | None = None,
    expires_in_days: int = 30,
) -> dict:
    get_user(conn, user_id)
    if discount_type not in ("FIXED", "PERCENT"):
        raise DomainError("INVALID_COUPON", f"지원하지 않는 쿠폰 타입입니다: {discount_type}")
    if value <= 0 or (discount_type == "PERCENT" and value > 100):
        raise DomainError("INVALID_COUPON", f"쿠폰 할인값이 올바르지 않습니다: {value}")
    code = f"QA-{secrets.token_hex(4).upper()}"
    expires_at = _ts(now() + timedelta(days=expires_in_days))
    with conn:
        cur = conn.execute(
            """INSERT INTO coupons (user_id, code, discount_type, value, min_order_amount, max_discount, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, code, discount_type, value, min_order_amount, max_discount, expires_at),
        )
    return dict(conn.execute("SELECT * FROM coupons WHERE id = ?", (cur.lastrowid,)).fetchone())


def calc_discount(coupon: dict, items_amount: int) -> int:
    if coupon["discount_type"] == "FIXED":
        discount = coupon["value"]
    else:
        discount = items_amount * coupon["value"] // 100
        if coupon["max_discount"] is not None:
            discount = min(discount, coupon["max_discount"])
    # 할인은 메뉴 합계까지만 (배달비는 할인 대상 아님)
    return min(discount, items_amount)


def _validate_coupon(conn: sqlite3.Connection, coupon_id: int, user_id: int, items_amount: int) -> dict:
    row = conn.execute("SELECT * FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
    if not row:
        raise DomainError("COUPON_NOT_FOUND", f"쿠폰을 찾을 수 없습니다: {coupon_id}", 404)
    coupon = dict(row)
    if coupon["user_id"] != user_id:
        raise DomainError("COUPON_NOT_OWNED", "본인 쿠폰만 사용할 수 있습니다.", 403)
    if coupon["used_order_id"] is not None:
        raise DomainError("COUPON_ALREADY_USED", "이미 사용한 쿠폰입니다.", 409)
    if datetime.fromisoformat(coupon["expires_at"]) <= now():
        raise DomainError("COUPON_EXPIRED", "만료된 쿠폰입니다.")
    if items_amount < coupon["min_order_amount"]:
        raise DomainError(
            "COUPON_MIN_ORDER_NOT_MET",
            f"쿠폰 최소주문금액({coupon['min_order_amount']}원)을 채우지 못했습니다.",
        )
    return coupon


# ---------- 주문 ----------

def create_order(
    conn: sqlite3.Connection,
    user_id: int,
    store_id: int,
    items: list[dict],
    coupon_id: int | None = None,
    actor: str = "customer",
) -> dict:
    if not items:
        raise DomainError("EMPTY_ORDER", "주문할 메뉴가 없습니다.")
    get_user(conn, user_id)
    store = get_store(conn, store_id)
    if not store["is_open"]:
        raise DomainError("STORE_CLOSED", f"영업 종료된 가게입니다: {store['name']}")

    lines = []
    items_amount = 0
    for item in items:
        quantity = item["quantity"]
        if not 1 <= quantity <= MAX_QUANTITY:
            raise DomainError("INVALID_QUANTITY", f"수량은 1~{MAX_QUANTITY}개만 가능합니다: {quantity}")
        menu = conn.execute("SELECT * FROM menus WHERE id = ?", (item["menu_id"],)).fetchone()
        if not menu:
            raise DomainError("MENU_NOT_FOUND", f"메뉴를 찾을 수 없습니다: {item['menu_id']}", 404)
        if menu["store_id"] != store_id:
            raise DomainError("MENU_NOT_IN_STORE", f"이 가게의 메뉴가 아닙니다: {menu['name']}")
        if menu["sold_out"]:
            raise DomainError("MENU_SOLD_OUT", f"품절된 메뉴입니다: {menu['name']}")
        lines.append((menu["id"], menu["name"], menu["price"], quantity))
        items_amount += menu["price"] * quantity

    if items_amount < store["min_order_amount"]:
        raise DomainError(
            "BELOW_MIN_ORDER_AMOUNT",
            f"최소주문금액({store['min_order_amount']}원)보다 적습니다: {items_amount}원",
        )

    discount = 0
    if coupon_id is not None:
        coupon = _validate_coupon(conn, coupon_id, user_id, items_amount)
        discount = calc_discount(coupon, items_amount)

    delivery_fee = store["delivery_fee"]
    total = items_amount + delivery_fee - discount
    ts = _ts()
    with conn:
        cur = conn.execute(
            """INSERT INTO orders (user_id, store_id, status, items_amount, delivery_fee, discount_amount,
                                   total_amount, coupon_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, store_id, PENDING, items_amount, delivery_fee, discount, total, coupon_id, ts, ts),
        )
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO order_items (order_id, menu_id, menu_name, unit_price, quantity) VALUES (?, ?, ?, ?, ?)",
            [(order_id, *line) for line in lines],
        )
        conn.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, actor, changed_at) "
            "VALUES (?, NULL, ?, ?, ?)",
            (order_id, PENDING, actor, ts),
        )
        if coupon_id is not None:
            conn.execute("UPDATE coupons SET used_order_id = ? WHERE id = ?", (order_id, coupon_id))
    return get_order(conn, order_id)


def change_status(
    conn: sqlite3.Connection,
    order_id: int,
    to_status: str,
    actor: str,
    cancel_reason: str | None = None,
) -> dict:
    if to_status not in STATUSES:
        raise DomainError("INVALID_STATUS", f"존재하지 않는 상태입니다: {to_status}")
    order = get_order(conn, order_id)
    from_status = order["status"]
    if to_status not in ALLOWED_TRANSITIONS[from_status]:
        raise DomainError(
            "INVALID_STATUS_TRANSITION",
            f"{from_status} -> {to_status} 로 변경할 수 없습니다.",
            409,
        )
    ts = _ts()
    with conn:
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ?, cancel_reason = COALESCE(?, cancel_reason) WHERE id = ?",
            (to_status, ts, cancel_reason, order_id),
        )
        conn.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, actor, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (order_id, from_status, to_status, actor, ts),
        )
        if to_status == CANCELED:
            # 취소된 주문에 쓴 쿠폰은 다시 쓸 수 있게 복구
            conn.execute("UPDATE coupons SET used_order_id = NULL WHERE used_order_id = ?", (order_id,))
    return get_order(conn, order_id)


def cancel_by_customer(conn: sqlite3.Connection, order_id: int, reason: str | None = None) -> dict:
    order = get_order(conn, order_id)
    if order["status"] != PENDING:
        raise DomainError(
            "CUSTOMER_CANCEL_NOT_ALLOWED",
            f"가게가 주문을 수락한 뒤에는 직접 취소할 수 없습니다(현재 {order['status']}).",
            409,
        )
    return change_status(conn, order_id, CANCELED, "customer", reason or "손님 취소")


def advance_to(conn: sqlite3.Connection, order_id: int, target: str, actor: str = "qa-tool") -> dict:
    """목표 상태까지 정상 전이를 한 단계씩 밟는다. DB 값을 직접 덮어쓰지 않으므로 이력이 실제 흐름과 같다."""
    if target not in STATUSES:
        raise DomainError("INVALID_STATUS", f"존재하지 않는 상태입니다: {target}")
    order = get_order(conn, order_id)
    current = order["status"]
    if current == target:
        return order
    if target == CANCELED:
        return change_status(conn, order_id, CANCELED, actor, "QA 도구 취소")
    if current == CANCELED or FLOW.index(target) < FLOW.index(current):
        raise DomainError(
            "INVALID_STATUS_TRANSITION",
            f"{current} 에서 {target} 로 되돌릴 수 없습니다.",
            409,
        )
    for status in FLOW[FLOW.index(current) + 1: FLOW.index(target) + 1]:
        order = change_status(conn, order_id, status, actor)
    return order
