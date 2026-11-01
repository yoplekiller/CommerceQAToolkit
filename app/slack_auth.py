"""Slack /qa 권한 · 사용량 제한 · 감사 로그.

서명 검증(slack_bot.verify_signature)은 "Slack 에서 온 요청인지"만 확인한다.
여기서는 "누가 보냈는지"(Slack 이 보내주는 user_id · channel_id)로 실행 가능 여부를 정한다.

권한 설정 (SLACK_PERMISSIONS 환경변수(YAML/JSON) > SLACK_PERMISSIONS_FILE > slack/permissions.yaml 순으로 읽음):

    default_role: none          # 등록 안 된 사람의 역할 (none 이면 help · 내권한만)
    channels: [C0123ABCD]       # 비어 있거나 없으면 모든 채널 허용
    users:
      U0123ABCD: admin
      U0456EFGH: tester

설정이 하나도 없으면 전부 잠긴다 (help · 내권한만 가능). 열어두는 것보다 잠가두는 쪽이 안전해서.
"""
import os
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path

import yaml

ROLES = ["none", "viewer", "tester", "admin"]
ROLE_LABEL = {"none": "권한 없음", "viewer": "조회만", "tester": "테스트 데이터 생성", "admin": "관리자"}

# 명령별 최소 역할
REQUIRED_ROLE = {
    "help": "none", "whoami": "none",
    "show": "viewer",
    "orders": "tester", "advance": "tester", "stock": "tester", "users": "tester", "coupon": "tester", "product": "tester",
    "seed": "admin", "audit": "admin",
}
# 사용량 제한은 데이터를 바꾸는 명령에만
RATE_LIMITED_ACTIONS = {"orders", "advance", "stock", "users", "coupon", "product", "seed"}

DEFAULT_PERMISSIONS_FILE = Path(__file__).resolve().parents[1] / "slack" / "permissions.yaml"


class PermissionDenied(Exception):
    """권한 부족 / 허용되지 않은 채널 / 사용량 초과."""


def load_permissions() -> dict:
    """권한 설정을 읽는다. 없으면 빈 설정(전부 잠김)."""
    raw = os.getenv("SLACK_PERMISSIONS")
    if raw:
        config = yaml.safe_load(raw)
    else:
        path = Path(os.getenv("SLACK_PERMISSIONS_FILE") or DEFAULT_PERMISSIONS_FILE)
        config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    config = config or {}
    config.setdefault("default_role", "none")
    config.setdefault("channels", [])
    config["users"] = {str(k): v for k, v in (config.get("users") or {}).items()}
    for who, role in [("default_role", config["default_role"]), *config["users"].items()]:
        if role not in ROLES:
            raise ValueError(f"알 수 없는 역할 '{role}' ({who}). 가능: {', '.join(ROLES)}")
    return config


def role_of(permissions: dict, user_id: str) -> str:
    return permissions["users"].get(user_id, permissions["default_role"])


def authorize(permissions: dict, user_id: str, channel_id: str, action: str) -> str:
    """실행 가능하면 사용자 역할을 돌려주고, 아니면 PermissionDenied (이유 포함)."""
    role = role_of(permissions, user_id)
    needed = REQUIRED_ROLE[action]
    if needed == "none":
        return role
    if permissions["channels"] and channel_id not in permissions["channels"]:
        raise PermissionDenied("이 채널에서는 /qa 를 쓸 수 없습니다. QA 채널에서 실행해 주세요.")
    if ROLES.index(role) < ROLES.index(needed):
        raise PermissionDenied(
            f"`{needed}` 권한이 필요합니다 (현재 `{role}` · {ROLE_LABEL[role]}). "
            f"관리자에게 사용자 ID `{user_id}` 등록을 요청하세요."
        )
    return role


class RateLimiter:
    """사용자별 1분당 실행 횟수 제한 (메모리 기반 — 서버 재시작 시 초기화)."""

    def __init__(self):
        self.calls: dict[str, deque] = defaultdict(deque)

    def check(self, user_id: str, limit: int, now: float | None = None, window: float = 60.0) -> None:
        now = now or time.time()
        calls = self.calls[user_id]
        while calls and now - calls[0] >= window:
            calls.popleft()
        if len(calls) >= limit:
            raise PermissionDenied(f"1분에 {limit}번까지 실행할 수 있습니다. 잠시 후 다시 시도하세요.")
        calls.append(now)

    def clear(self) -> None:
        self.calls.clear()


limiter = RateLimiter()


def rate_limit_per_minute() -> int:
    return int(os.getenv("SLACK_RATE_LIMIT_PER_MINUTE", "10"))


def record(conn: sqlite3.Connection, user: dict, command: str, result: str, detail: str = "") -> None:
    """감사 로그 한 줄 (허용 · 거부 · 실패 모두)."""
    from app.services import _ts
    with conn:
        conn.execute(
            "INSERT INTO audit_log (at, source, user_id, user_name, channel_id, command, result, detail) "
            "VALUES (?, 'slack', ?, ?, ?, ?, ?, ?)",
            (_ts(), user.get("id", ""), user.get("name"), user.get("channel"), command, result, detail[:500]),
        )


def recent(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
