import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price INTEGER NOT NULL,
    stock INTEGER NOT NULL CHECK (stock >= 0),
    max_per_order INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_test INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    code TEXT NOT NULL UNIQUE,
    discount_type TEXT NOT NULL CHECK (discount_type IN ('FIXED', 'PERCENT')),
    value INTEGER NOT NULL,
    min_order_amount INTEGER NOT NULL DEFAULT 0,
    max_discount INTEGER,
    expires_at TEXT NOT NULL,
    used_order_id INTEGER
);

CREATE TABLE IF NOT EXISTS cart_items (
    user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL,
    items_amount INTEGER NOT NULL,
    shipping_fee INTEGER NOT NULL,
    discount_amount INTEGER NOT NULL,
    total_amount INTEGER NOT NULL,
    coupon_id INTEGER REFERENCES coupons(id),
    cancel_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    unit_price INTEGER NOT NULL,
    quantity INTEGER NOT NULL
);

-- 감사 로그: 누가 언제 어떤 QA 명령을 실행했는지. 데이터 초기화(reset_all)에도 지우지 않는다.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT,
    channel_id TEXT,
    command TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS order_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""

# 외래키 순서상 자식 테이블부터 지운다 (audit_log 는 초기화 대상이 아님)
TABLES = ["order_status_history", "order_items", "orders", "cart_items", "coupons", "users", "products"]


def get_db_path() -> str:
    """DB 파일 경로. 환경변수 DB_PATH 가 없으면 commerce.db."""
    return os.getenv("DB_PATH", "commerce.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    """SQLite 연결을 연다.

    결과를 dict 처럼 꺼낼 수 있게 Row 팩토리를 쓰고, 외래키 검사를 켠다(SQLite 는 기본으로 꺼져 있음).
    """
    conn = sqlite3.connect(path or get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """테이블이 없으면 만든다. 이미 있으면 그대로 둔다."""
    conn.executescript(SCHEMA)
    conn.commit()


def reset_all(conn: sqlite3.Connection) -> None:
    """모든 테이블을 지우고 다시 만든다. 테이블을 DROP 하므로 ID 번호도 1부터 다시 시작한다."""
    with conn:
        for table in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_schema(conn)
