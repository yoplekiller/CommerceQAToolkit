"""QA 도구: 시드/초기화, 테스트 계정·쿠폰 발급, 재고 조정, 장바구니 자동 담기, 주문 자동생성/자동완료.

주문/장바구니는 services 함수를 그대로 거치므로, 도구로 만든 데이터도 서비스 규칙을 통과한 데이터다.
"""
import random
import secrets
import sqlite3

from app import services
from app.db import reset_all
from app.services import FREE_SHIPPING_THRESHOLD, DomainError

# (이름, 가격, 재고, 1회 구매제한, 판매중)
SEED_PRODUCTS = [
    ("무선 이어폰", 89000, 50, None, True),
    ("생수 2L x 6", 5980, 500, None, True),
    ("USB-C 케이블", 9900, 200, None, True),
    ("한정판 스니커즈", 159000, 3, 1, True),     # 재고 적음 + 1인 1개 제한
    ("스테인리스 텀블러", 25000, 0, None, True),  # 품절
    ("보조배터리", 32000, 30, None, False),       # 판매중지
    ("화장지 30롤", 19800, 100, None, True),       # 무료배송 기준 금액과 같은 가격
    ("볼펜", 1000, 1000, None, True),
]

SCENARIOS = ("basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping")


def reset(conn: sqlite3.Connection) -> None:
    """모든 데이터를 지우고 빈 테이블만 남긴다."""
    reset_all(conn)


def seed(conn: sqlite3.Connection) -> dict:
    """전체 초기화 후 기본 상품을 넣는다. 매번 같은 ID로 만들어지므로 테스트에서 ID를 고정해 써도 된다."""
    reset_all(conn)
    with conn:
        conn.executemany(
            "INSERT INTO products (name, price, stock, max_per_order, is_active) VALUES (?, ?, ?, ?, ?)",
            [(name, price, stock, limit, int(active)) for name, price, stock, limit, active in SEED_PRODUCTS],
        )
    return {"products": services.list_products(conn)}


def bulk_users(conn: sqlite3.Connection, count: int, prefix: str = "qa") -> list[dict]:
    """테스트 계정을 count 개 만든다. 이메일은 prefix+랜덤@test.local 형식이라 겹치지 않는다."""
    users = []
    for _ in range(count):
        tag = secrets.token_hex(4)
        users.append(services.create_user(conn, f"{prefix}+{tag}@test.local", f"{prefix}-{tag}", is_test=True))
    return users


def set_stock(conn: sqlite3.Connection, product_id: int, stock: int) -> dict:
    """품절/재고 1개 같은 상황을 바로 만든다."""
    if stock < 0:
        raise DomainError("INVALID_STOCK", f"재고는 0 이상이어야 합니다: {stock}")
    services.get_product(conn, product_id)
    with conn:
        conn.execute("UPDATE products SET stock = ? WHERE id = ?", (stock, product_id))
    return services.get_product(conn, product_id)


def create_product(
    conn: sqlite3.Connection,
    name: str,
    price: int,
    stock: int,
    max_per_order: int | None = None,
    is_active: bool = True,
) -> dict:
    """테스트용 상품을 바로 만든다. 시드 가격으로 만들 수 없는 경계값(예: 19,799원) 테스트용."""
    if price < 0 or stock < 0:
        raise DomainError("INVALID_PRODUCT", f"가격과 재고는 0 이상이어야 합니다: price={price}, stock={stock}")
    with conn:
        cur = conn.execute(
            "INSERT INTO products (name, price, stock, max_per_order, is_active) VALUES (?, ?, ?, ?, ?)",
            (name, price, stock, max_per_order, int(is_active)),
        )
    return services.get_product(conn, cur.lastrowid)


def _orderable_products(conn: sqlite3.Connection) -> list[dict]:
    """지금 주문할 수 있는 상품 (판매중 + 재고 있음)."""
    return [p for p in services.list_products(conn) if p["is_active"] and p["stock"] > 0]


def _max_quantity(product: dict) -> int:
    """이 상품을 한 주문에 최대 몇 개 담을 수 있는지 (재고, 수량 상한 99, 1회 구매 제한 중 가장 작은 값)."""
    limits = [product["stock"], services.MAX_QUANTITY]
    if product["max_per_order"] is not None:
        limits.append(product["max_per_order"])
    return min(limits)


def _pick_items(rng: random.Random, products: list[dict], scenario: str) -> list[dict]:
    """시나리오에 맞게 주문할 상품과 수량을 고른다. 재고와 구매 제한을 넘지 않는다."""
    if scenario == "paid_shipping":
        cheap = [p for p in products if p["price"] < FREE_SHIPPING_THRESHOLD]
        if not cheap:
            raise DomainError("SCENARIO_NOT_APPLICABLE", "무료배송 기준보다 싼 주문 가능 상품이 없습니다.", 409)
        return [{"product_id": rng.choice(cheap)["id"], "quantity": 1}]

    kinds = 2 if scenario == "multi_item" else 1
    if len(products) < kinds:
        raise DomainError("SCENARIO_NOT_APPLICABLE", f"주문 가능한 상품이 {kinds}종 미만입니다.", 409)
    chosen = rng.sample(products, k=kinds)
    quantities = {p["id"]: 1 for p in chosen}

    if scenario == "free_shipping":
        # 무료배송 기준을 넘을 때까지 수량을 늘린다 (재고/구매제한 안에서)
        by_id = {p["id"]: p for p in chosen}
        while sum(by_id[pid]["price"] * q for pid, q in quantities.items()) < FREE_SHIPPING_THRESHOLD:
            growable = [pid for pid, q in quantities.items() if q < _max_quantity(by_id[pid])]
            if not growable:
                raise DomainError("SCENARIO_NOT_APPLICABLE", "재고 안에서 무료배송 기준을 채울 수 없습니다.", 409)
            quantities[rng.choice(growable)] += 1
    return [{"product_id": pid, "quantity": q} for pid, q in quantities.items()]


def fill_cart(conn: sqlite3.Connection, user_id: int, count: int, seed_value: int | None = None) -> list[dict]:
    """주문 가능한 상품 중 count 종류를 1개씩 장바구니에 담는다."""
    rng = random.Random(seed_value)
    products = _orderable_products(conn)
    if len(products) < count:
        raise DomainError("SCENARIO_NOT_APPLICABLE", f"주문 가능한 상품이 {count}종 미만입니다.", 409)
    for product in rng.sample(products, k=count):
        services.add_to_cart(conn, user_id, product["id"], 1)
    return services.get_cart(conn, user_id)


def generate_orders(
    conn: sqlite3.Connection,
    count: int,
    scenario: str = "basic",
    target_status: str = services.PAID,
    user_id: int | None = None,
    seed_value: int | None = None,
) -> list[dict]:
    """주문 자동생성: count 개를 시나리오대로 만들고 target_status 까지 자동 진행한다.

    user_id 를 안 주면 테스트 계정을 하나 만들어 모든 주문에 쓴다.
    쿠폰 시나리오는 주문마다 새 쿠폰을 발급해서 쓴다.
    seed_value 가 같으면 같은 상품 조합이 나온다 (같은 시드 데이터 기준).
    """
    if scenario not in SCENARIOS:
        raise DomainError("INVALID_SCENARIO", f"지원하지 않는 시나리오입니다: {scenario} (가능: {', '.join(SCENARIOS)})")
    if target_status not in services.STATUSES:
        raise DomainError("INVALID_STATUS", f"존재하지 않는 상태입니다: {target_status}")

    rng = random.Random(seed_value)
    if user_id is None:
        user_id = bulk_users(conn, 1, prefix="qa-order")[0]["id"]
    else:
        services.get_user(conn, user_id)

    orders = []
    for _ in range(count):
        # 앞 주문에서 재고가 빠졌을 수 있으니 매번 다시 조회
        products = _orderable_products(conn)
        if not products:
            raise DomainError("NO_ORDERABLE_PRODUCT", "주문 가능한 상품이 없습니다. seed 를 실행하거나 재고를 채우세요.", 409)
        items = _pick_items(rng, products, scenario)

        coupon_id = None
        if scenario == "coupon_fixed":
            coupon_id = services.issue_coupon(conn, user_id, "FIXED", 3000)["id"]
        elif scenario == "coupon_percent":
            coupon_id = services.issue_coupon(conn, user_id, "PERCENT", 10, max_discount=5000)["id"]

        order = services.create_order(conn, user_id, items, coupon_id, actor="qa-tool")
        orders.append(services.advance_to(conn, order["id"], target_status))
    return orders


def list_orders(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    """최근 주문 목록 (최신순). status 를 주면 그 상태만. 웹 화면 주문 표에서 쓴다."""
    if status is not None and status not in services.STATUSES:
        raise DomainError("INVALID_STATUS", f"존재하지 않는 상태입니다: {status}")
    query = "SELECT id FROM orders"
    params: list = []
    if status is not None:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [services.get_order(conn, row["id"]) for row in conn.execute(query, params)]
