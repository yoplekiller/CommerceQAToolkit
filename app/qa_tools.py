"""QA 도구: 시드/초기화, 테스트 계정·쿠폰 발급, 주문 자동생성/자동완료.

주문은 services.create_order / advance_to 를 그대로 거치므로, 도구로 만든 데이터도 서비스 규칙을 통과한 데이터다.
"""
import random
import secrets
import sqlite3

from app import services
from app.db import reset_all
from app.services import DomainError

SEED_STORES = [
    {
        "name": "QA치킨", "min_order_amount": 15000, "delivery_fee": 3000, "is_open": True,
        "menus": [("후라이드", 18000, False), ("양념", 19000, False), ("콜라", 2000, False), ("치즈볼", 5000, True)],
    },
    {
        "name": "QA분식", "min_order_amount": 12000, "delivery_fee": 2000, "is_open": True,
        "menus": [("떡볶이", 5000, False), ("순대", 4000, False), ("김밥", 3500, False), ("튀김", 3000, False)],
    },
    {
        "name": "QA피자", "min_order_amount": 20000, "delivery_fee": 0, "is_open": False,
        "menus": [("페퍼로니", 22000, False)],
    },
    {
        "name": "QA카페", "min_order_amount": 0, "delivery_fee": 1500, "is_open": True,
        "menus": [("아메리카노", 4500, False), ("라떼", 5000, False)],
    },
]

SCENARIOS = ("basic", "multi_item", "coupon_fixed", "coupon_percent")


def reset(conn: sqlite3.Connection) -> None:
    reset_all(conn)


def seed(conn: sqlite3.Connection) -> dict:
    """전체 초기화 후 기본 가게/메뉴를 넣는다. 매번 같은 ID로 만들어지므로 테스트에서 ID를 고정해 써도 된다."""
    reset_all(conn)
    with conn:
        for store in SEED_STORES:
            cur = conn.execute(
                "INSERT INTO stores (name, min_order_amount, delivery_fee, is_open) VALUES (?, ?, ?, ?)",
                (store["name"], store["min_order_amount"], store["delivery_fee"], int(store["is_open"])),
            )
            conn.executemany(
                "INSERT INTO menus (store_id, name, price, sold_out) VALUES (?, ?, ?, ?)",
                [(cur.lastrowid, name, price, int(sold_out)) for name, price, sold_out in store["menus"]],
            )
    return {"stores": services.list_stores(conn)}


def bulk_users(conn: sqlite3.Connection, count: int, prefix: str = "qa") -> list[dict]:
    users = []
    for _ in range(count):
        tag = secrets.token_hex(4)
        users.append(services.create_user(conn, f"{prefix}+{tag}@test.local", f"{prefix}-{tag}", is_test=True))
    return users


def _pick_items(rng: random.Random, menus: list[dict], min_order_amount: int, min_kinds: int) -> list[dict]:
    """최소 min_kinds 종류를 담고, 최소주문금액을 넘을 때까지 수량을 늘린다."""
    if len(menus) < min_kinds:
        raise DomainError("SCENARIO_NOT_APPLICABLE", f"주문 가능한 메뉴가 {min_kinds}종 미만인 가게입니다.")
    chosen = rng.sample(menus, k=rng.randint(min_kinds, min(len(menus), min_kinds + 1)))
    quantities = {m["id"]: 1 for m in chosen}
    prices = {m["id"]: m["price"] for m in chosen}
    while sum(prices[mid] * q for mid, q in quantities.items()) < min_order_amount:
        quantities[rng.choice(list(quantities))] += 1
    return [{"menu_id": mid, "quantity": q} for mid, q in quantities.items()]


def generate_orders(
    conn: sqlite3.Connection,
    count: int,
    scenario: str = "basic",
    target_status: str = services.PENDING,
    user_id: int | None = None,
    store_id: int | None = None,
    seed_value: int | None = None,
) -> list[dict]:
    if scenario not in SCENARIOS:
        raise DomainError("INVALID_SCENARIO", f"지원하지 않는 시나리오입니다: {scenario} (가능: {', '.join(SCENARIOS)})")
    if target_status not in services.STATUSES:
        raise DomainError("INVALID_STATUS", f"존재하지 않는 상태입니다: {target_status}")

    rng = random.Random(seed_value)
    if user_id is None:
        user_id = bulk_users(conn, 1, prefix="qa-order")[0]["id"]
    else:
        services.get_user(conn, user_id)

    if store_id is not None:
        store = services.get_store(conn, store_id)
        if not store["is_open"]:
            raise DomainError("STORE_CLOSED", f"영업 종료된 가게에는 주문을 만들 수 없습니다: {store['name']}")
        candidates = [store]
    else:
        candidates = [s for s in services.list_stores(conn) if s["is_open"]]
        if not candidates:
            raise DomainError("NO_OPEN_STORE", "영업 중인 가게가 없습니다. 먼저 seed 를 실행하세요.", 409)

    min_kinds = 2 if scenario == "multi_item" else 1
    orders = []
    for _ in range(count):
        store = rng.choice(candidates)
        menus = [m for m in services.list_menus(conn, store["id"]) if not m["sold_out"]]
        items = _pick_items(rng, menus, store["min_order_amount"], min_kinds)

        coupon_id = None
        if scenario == "coupon_fixed":
            coupon_id = services.issue_coupon(conn, user_id, "FIXED", 3000)["id"]
        elif scenario == "coupon_percent":
            coupon_id = services.issue_coupon(conn, user_id, "PERCENT", 10, max_discount=5000)["id"]

        order = services.create_order(conn, user_id, store["id"], items, coupon_id, actor="qa-tool")
        orders.append(services.advance_to(conn, order["id"], target_status))
    return orders
