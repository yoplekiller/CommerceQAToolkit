"""커머스 주문 도메인 로직. API 라우터와 QA 도구가 모두 이 함수들을 거친다."""
import secrets
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone

PAID = "PAID"                          # 결제완료
PREPARING = "PREPARING"                # 상품준비중
SHIPPING = "SHIPPING"                  # 배송중
DELIVERED = "DELIVERED"                # 배송완료
CONFIRMED = "CONFIRMED"                # 구매확정
CANCELED = "CANCELED"                  # 주문취소
RETURN_REQUESTED = "RETURN_REQUESTED"  # 반품요청
RETURNED = "RETURNED"                  # 반품완료

STATUSES = [PAID, PREPARING, SHIPPING, DELIVERED, CONFIRMED, CANCELED, RETURN_REQUESTED, RETURNED]

ALLOWED_TRANSITIONS = {
    PAID: {PREPARING, CANCELED},
    PREPARING: {SHIPPING, CANCELED},
    SHIPPING: {DELIVERED},
    DELIVERED: {CONFIRMED, RETURN_REQUESTED},
    RETURN_REQUESTED: {RETURNED},
    CONFIRMED: set(),
    CANCELED: set(),
    RETURNED: set(),
}

# 재고/쿠폰을 되돌리는 종료 상태
RESTORING_STATUSES = {CANCELED, RETURNED}

MAX_QUANTITY = 99
FREE_SHIPPING_THRESHOLD = 19800
SHIPPING_FEE = 3000


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


# ---------- 상품 ----------

def list_products(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY id")]


def get_product(conn: sqlite3.Connection, product_id: int) -> dict:
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise DomainError("PRODUCT_NOT_FOUND", f"상품을 찾을 수 없습니다: {product_id}", 404)
    return dict(row)


def _check_orderable(product: dict, quantity: int) -> None:
    if not 1 <= quantity <= MAX_QUANTITY:
        raise DomainError("INVALID_QUANTITY", f"수량은 1~{MAX_QUANTITY}개만 가능합니다: {quantity}")
    if not product["is_active"]:
        raise DomainError("PRODUCT_INACTIVE", f"판매 중지된 상품입니다: {product['name']}")
    if product["stock"] == 0:
        raise DomainError("SOLD_OUT", f"품절된 상품입니다: {product['name']}", 409)
    if quantity > product["stock"]:
        raise DomainError(
            "INSUFFICIENT_STOCK",
            f"재고가 부족합니다: {product['name']} (요청 {quantity}, 재고 {product['stock']})",
            409,
        )
    if product["max_per_order"] is not None and quantity > product["max_per_order"]:
        raise DomainError(
            "PURCHASE_LIMIT_EXCEEDED",
            f"1회 최대 {product['max_per_order']}개까지 구매할 수 있습니다: {product['name']}",
        )


# ---------- 회원 / 쿠폰 ----------

def get_user(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise DomainError("USER_NOT_FOUND", f"회원을 찾을 수 없습니다: {user_id}", 404)
    return dict(row)


def create_user(conn: sqlite3.Connection, email: str, name: str, is_test: bool = False) -> dict:
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise DomainError("EMAIL_DUPLICATED", f"이미 가입된 이메일입니다: {email}", 409)
    with conn:
        cur = conn.execute(
            "INSERT INTO users (email, name, is_test, created_at) VALUES (?, ?, ?, ?)",
            (email, name, int(is_test), _ts()),
        )
    return get_user(conn, cur.lastrowid)


def list_coupons(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    get_user(conn, user_id)
    return [dict(r) for r in conn.execute("SELECT * FROM coupons WHERE user_id = ? ORDER BY id", (user_id,))]


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
    # 할인은 상품 합계까지만 (배송비는 할인 대상 아님)
    return min(discount, items_amount)


def calc_shipping_fee(items_amount: int) -> int:
    # 무료배송 기준은 쿠폰 할인 전 상품 합계
    return 0 if items_amount >= FREE_SHIPPING_THRESHOLD else SHIPPING_FEE


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


# ---------- 장바구니 ----------

def get_cart(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    get_user(conn, user_id)
    return [
        dict(r) for r in conn.execute(
            """SELECT c.product_id, p.name AS product_name, p.price AS unit_price, c.quantity
               FROM cart_items c JOIN products p ON p.id = c.product_id
               WHERE c.user_id = ? ORDER BY c.added_at, c.product_id""",
            (user_id,),
        )
    ]


def add_to_cart(conn: sqlite3.Connection, user_id: int, product_id: int, quantity: int) -> list[dict]:
    get_user(conn, user_id)
    product = get_product(conn, product_id)
    row = conn.execute(
        "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?", (user_id, product_id)
    ).fetchone()
    total = quantity + (row["quantity"] if row else 0)
    # 같은 상품을 다시 담으면 수량이 합쳐지고, 합친 수량 기준으로 재고/구매제한을 검사한다
    _check_orderable(product, total)
    with conn:
        conn.execute(
            """INSERT INTO cart_items (user_id, product_id, quantity, added_at) VALUES (?, ?, ?, ?)
               ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
            (user_id, product_id, total, _ts()),
        )
    return get_cart(conn, user_id)


def remove_from_cart(conn: sqlite3.Connection, user_id: int, product_id: int) -> list[dict]:
    get_user(conn, user_id)
    with conn:
        conn.execute("DELETE FROM cart_items WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    return get_cart(conn, user_id)


# ---------- 주문 ----------

def get_order(conn: sqlite3.Connection, order_id: int) -> dict:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise DomainError("ORDER_NOT_FOUND", f"주문을 찾을 수 없습니다: {order_id}", 404)
    order = dict(row)
    order["items"] = [
        dict(r) for r in conn.execute(
            "SELECT product_id, product_name, unit_price, quantity FROM order_items WHERE order_id = ? ORDER BY id",
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


def create_order(
    conn: sqlite3.Connection,
    user_id: int,
    items: list[dict],
    coupon_id: int | None = None,
    actor: str = "customer",
) -> dict:
    """주문 생성 = 결제 완료(모의 결제). 재고 차감과 쿠폰 사용이 같은 트랜잭션에서 처리된다."""
    if not items:
        raise DomainError("EMPTY_ORDER", "주문할 상품이 없습니다.")
    get_user(conn, user_id)

    # 같은 상품이 여러 줄로 오면 합친 수량 기준으로 검사
    quantities: dict[int, int] = {}
    for item in items:
        if not 1 <= item["quantity"] <= MAX_QUANTITY:
            raise DomainError("INVALID_QUANTITY", f"수량은 1~{MAX_QUANTITY}개만 가능합니다: {item['quantity']}")
        quantities[item["product_id"]] = quantities.get(item["product_id"], 0) + item["quantity"]

    lines = []
    items_amount = 0
    for product_id, quantity in quantities.items():
        product = get_product(conn, product_id)
        _check_orderable(product, quantity)
        lines.append((product["id"], product["name"], product["price"], quantity))
        items_amount += product["price"] * quantity

    discount = 0
    if coupon_id is not None:
        coupon = _validate_coupon(conn, coupon_id, user_id, items_amount)
        discount = calc_discount(coupon, items_amount)

    shipping_fee = calc_shipping_fee(items_amount)
    total = items_amount + shipping_fee - discount
    ts = _ts()
    with conn:
        for product_id, _, _, quantity in lines:
            # 조회 시점과 차감 시점 사이에 재고가 줄었을 수 있어서 조건부 UPDATE 로 초과 판매를 막는다
            cur = conn.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                (quantity, product_id, quantity),
            )
            if cur.rowcount == 0:
                raise DomainError("INSUFFICIENT_STOCK", f"재고가 부족합니다: 상품 {product_id}", 409)
        cur = conn.execute(
            """INSERT INTO orders (user_id, status, items_amount, shipping_fee, discount_amount,
                                   total_amount, coupon_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, PAID, items_amount, shipping_fee, discount, total, coupon_id, ts, ts),
        )
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO order_items (order_id, product_id, product_name, unit_price, quantity) VALUES (?, ?, ?, ?, ?)",
            [(order_id, *line) for line in lines],
        )
        conn.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, actor, changed_at) "
            "VALUES (?, NULL, ?, ?, ?)",
            (order_id, PAID, actor, ts),
        )
        if coupon_id is not None:
            conn.execute("UPDATE coupons SET used_order_id = ? WHERE id = ?", (order_id, coupon_id))
    return get_order(conn, order_id)


def checkout_cart(conn: sqlite3.Connection, user_id: int, coupon_id: int | None = None) -> dict:
    cart = get_cart(conn, user_id)
    if not cart:
        raise DomainError("EMPTY_CART", "장바구니가 비어 있습니다.")
    order = create_order(conn, user_id, [{"product_id": c["product_id"], "quantity": c["quantity"]} for c in cart],
                         coupon_id)
    with conn:
        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    return order


def change_status(
    conn: sqlite3.Connection,
    order_id: int,
    to_status: str,
    actor: str,
    reason: str | None = None,
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
            (to_status, ts, reason, order_id),
        )
        conn.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, actor, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (order_id, from_status, to_status, actor, ts),
        )
        if to_status in RESTORING_STATUSES:
            # 취소/반품완료 시 재고와 쿠폰을 되돌린다
            conn.executemany(
                "UPDATE products SET stock = stock + ? WHERE id = ?",
                [(i["quantity"], i["product_id"]) for i in order["items"]],
            )
            conn.execute("UPDATE coupons SET used_order_id = NULL WHERE used_order_id = ?", (order_id,))
    return get_order(conn, order_id)


def cancel_by_customer(conn: sqlite3.Connection, order_id: int, reason: str | None = None) -> dict:
    order = get_order(conn, order_id)
    if order["status"] not in (PAID, PREPARING):
        raise DomainError(
            "CANCEL_NOT_ALLOWED",
            f"배송이 시작된 주문은 취소할 수 없습니다. 반품을 요청하세요(현재 {order['status']}).",
            409,
        )
    return change_status(conn, order_id, CANCELED, "customer", reason or "고객 취소")


def request_return(conn: sqlite3.Connection, order_id: int, reason: str | None = None) -> dict:
    order = get_order(conn, order_id)
    if order["status"] != DELIVERED:
        raise DomainError(
            "RETURN_NOT_ALLOWED",
            f"배송완료 상태에서만 반품을 요청할 수 있습니다(현재 {order['status']}).",
            409,
        )
    return change_status(conn, order_id, RETURN_REQUESTED, "customer", reason or "고객 반품 요청")


def confirm_purchase(conn: sqlite3.Connection, order_id: int) -> dict:
    return change_status(conn, order_id, CONFIRMED, "customer")


def find_path(current: str, target: str) -> list[str] | None:
    """허용된 전이만으로 current -> target 까지 가는 가장 짧은 경로 (current 제외). 갈 수 없으면 None."""
    queue = deque([(current, [])])
    seen = {current}
    while queue:
        status, path = queue.popleft()
        if status == target:
            return path
        for nxt in sorted(ALLOWED_TRANSITIONS[status]):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
    return None


def advance_to(conn: sqlite3.Connection, order_id: int, target: str, actor: str = "qa-tool") -> dict:
    """목표 상태까지 허용된 전이를 한 단계씩 밟는다. DB 값을 직접 덮어쓰지 않으므로 이력과 재고/쿠폰 처리가 실제 흐름과 같다."""
    if target not in STATUSES:
        raise DomainError("INVALID_STATUS", f"존재하지 않는 상태입니다: {target}")
    order = get_order(conn, order_id)
    path = find_path(order["status"], target)
    if path is None:
        raise DomainError(
            "INVALID_STATUS_TRANSITION",
            f"{order['status']} 에서 {target} 로 갈 수 없습니다.",
            409,
        )
    for status in path:
        order = change_status(conn, order_id, status, actor)
    return order
