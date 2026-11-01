"""Slack 슬래시 커맨드(/qa)로 QA 도구를 쓰게 해주는 모듈.

    /qa 주문 3건 정률쿠폰 배송완료      → 주문 자동생성 + 자동완료
    /qa 상태 12 반품완료                → 주문 상태 자동완료
    /qa 조회 12                         → 주문 상세 + 이력
    /qa 재고 4 0                        → 재고 설정 (0 = 품절)
    /qa 상품 테스트 머그컵 19799 5        → 상품 등록 (판매자 등록과 같은 검사)
    /qa 계정 3                          → 테스트 계정 생성
    /qa 쿠폰 5 정액 3000 [만료일수]      → 쿠폰 발급
    /qa 초기화 확인                      → 기본 데이터로 초기화

명령어 해석(parse_command) → 실행(execute) → Slack 메시지(Block Kit) 만들기 순서로 나눠서,
해석과 메시지 형식은 Slack 없이 단위 테스트할 수 있게 했다. 실행은 QA 도구와 같은 함수(qa_tools / services)를 쓴다.
"""
import hashlib
import hmac
import os
import re
import sqlite3
import time

from app import qa_tools, services, slack_auth
from app.services import DomainError

SCENARIO_ALIASES = {
    "기본": "basic", "여러상품": "multi_item", "다건": "multi_item",
    "쿠폰": "coupon_fixed", "정액쿠폰": "coupon_fixed", "정률쿠폰": "coupon_percent",
    "무료배송": "free_shipping", "유료배송": "paid_shipping",
    **{s: s for s in qa_tools.SCENARIOS},
}
SCENARIO_LABEL = {
    "basic": "기본", "multi_item": "여러상품", "coupon_fixed": "정액쿠폰",
    "coupon_percent": "정률쿠폰", "free_shipping": "무료배송", "paid_shipping": "유료배송",
}
STATUS_ALIASES = {
    "결제완료": "PAID", "상품준비": "PREPARING", "상품준비중": "PREPARING", "배송중": "SHIPPING",
    "배송완료": "DELIVERED", "구매확정": "CONFIRMED", "취소": "CANCELED", "주문취소": "CANCELED",
    "반품요청": "RETURN_REQUESTED", "반품완료": "RETURNED",
    **{s.lower(): s for s in services.STATUSES},
}
STATUS_LABEL = {
    "PAID": "결제완료", "PREPARING": "상품준비중", "SHIPPING": "배송중", "DELIVERED": "배송완료",
    "CONFIRMED": "구매확정", "CANCELED": "주문취소", "RETURN_REQUESTED": "반품요청", "RETURNED": "반품완료",
}
COUPON_TYPE_ALIASES = {"정액": "FIXED", "정률": "PERCENT", "fixed": "FIXED", "percent": "PERCENT"}

MAX_ORDERS_PER_COMMAND = 20
SIGNATURE_MAX_AGE_SECONDS = 60 * 5

HELP_TEXT = (
    "*QA 도구 슬래시 커맨드*\n"
    "• `/qa 주문 [N건] [시나리오] [상태] [회원=ID]` 주문 자동생성 + 목표 상태까지 진행\n"
    "   시나리오: 기본 · 여러상품 · 정액쿠폰 · 정률쿠폰 · 무료배송 · 유료배송\n"
    "   상태: 결제완료 · 상품준비 · 배송중 · 배송완료 · 구매확정 · 취소 · 반품요청 · 반품완료\n"
    "   예) `/qa 주문 3건 정률쿠폰 배송완료`\n"
    "• `/qa 상태 <주문ID> <상태>` 주문 상태 자동완료  예) `/qa 상태 12 반품완료`\n"
    "• `/qa 조회 <주문ID>` 주문 상세 + 처리 이력\n"
    "• `/qa 재고 <상품ID> <수량>` 재고 설정 (0 = 품절)\n"
    "• `/qa 상품 <이름> <가격> <재고>` 상품 등록 (이름에 공백 가능)  예) `/qa 상품 테스트 머그컵 19799 5`\n"
    "• `/qa 계정 [N]` 테스트 계정 생성\n"
    "• `/qa 쿠폰 <회원ID> <정액|정률> <값> [만료일수]` 쿠폰 발급 (만료일수 음수 = 만료 쿠폰)\n"
    "• `/qa 초기화 확인` 모든 데이터를 지우고 기본 상품으로 초기화 _(admin)_\n"
    "• `/qa 내권한` 내 Slack ID · 역할 확인 · `/qa 로그 [N]` 최근 실행 기록 _(admin)_\n"
    "권한: 조회 = viewer, 데이터 생성/변경 = tester, 초기화 · 로그 = admin"
)


class CommandError(Exception):
    """사용자가 명령어를 잘못 입력함 (도움말과 함께 보여줄 메시지)."""


# ---------- 요청 검증 ----------

def verify_signature(signing_secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    """Slack 요청 서명 검증 (v0=HMAC-SHA256("v0:{timestamp}:{body}")).

    5분보다 오래된 요청은 재전송 공격으로 보고 거부한다.
    """
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now or time.time()) - ts) > SIGNATURE_MAX_AGE_SECONDS:
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def get_signing_secret() -> str | None:
    """SLACK_SIGNING_SECRET 이 없으면 None → Slack 연동이 꺼진 상태."""
    return os.getenv("SLACK_SIGNING_SECRET") or None


# ---------- 명령어 해석 ----------

def _int(token: str, what: str) -> int:
    try:
        return int(token)
    except ValueError:
        raise CommandError(f"{what}는 숫자여야 합니다: `{token}`") from None


def _status(token: str) -> str:
    status = STATUS_ALIASES.get(token.lower())
    if status is None:
        raise CommandError(f"알 수 없는 상태입니다: `{token}`")
    return status


def parse_command(text: str) -> dict:
    """'/qa' 뒤 텍스트를 {"action": ..., 인자...} 로 바꾼다. 잘못된 입력은 CommandError."""
    tokens = text.split()
    if not tokens or tokens[0] in ("help", "도움말", "?"):
        return {"action": "help"}
    command, args = tokens[0], tokens[1:]

    if command == "주문":
        parsed = {"action": "orders", "count": 1, "scenario": "basic", "target_status": "PAID", "user_id": None}
        for token in args:
            count = re.fullmatch(r"(\d+)(건|개)?", token)
            user = re.fullmatch(r"회원[=:]?(\d+)", token)
            if count:
                parsed["count"] = int(count.group(1))
            elif user:
                parsed["user_id"] = int(user.group(1))
            elif token in SCENARIO_ALIASES:
                parsed["scenario"] = SCENARIO_ALIASES[token]
            elif token.lower() in STATUS_ALIASES:
                parsed["target_status"] = STATUS_ALIASES[token.lower()]
            else:
                raise CommandError(f"알 수 없는 옵션입니다: `{token}`")
        if not 1 <= parsed["count"] <= MAX_ORDERS_PER_COMMAND:
            raise CommandError(f"주문은 한 번에 1~{MAX_ORDERS_PER_COMMAND}건까지 만들 수 있습니다.")
        return parsed

    if command == "상태":
        if len(args) != 2:
            raise CommandError("사용법: `/qa 상태 <주문ID> <상태>`")
        return {"action": "advance", "order_id": _int(args[0], "주문ID"), "target_status": _status(args[1])}

    if command == "조회":
        if len(args) != 1:
            raise CommandError("사용법: `/qa 조회 <주문ID>`")
        return {"action": "show", "order_id": _int(args[0], "주문ID")}

    if command == "재고":
        if len(args) != 2:
            raise CommandError("사용법: `/qa 재고 <상품ID> <수량>`")
        return {"action": "stock", "product_id": _int(args[0], "상품ID"), "stock": _int(args[1], "수량")}

    if command == "상품":
        # 이름에 공백이 들어갈 수 있어서 뒤의 두 토큰을 가격 · 재고로, 나머지를 이름으로 본다
        if len(args) < 3:
            raise CommandError("사용법: `/qa 상품 <이름> <가격> <재고>`")
        return {"action": "product", "name": " ".join(args[:-2]),
                "price": _int(args[-2], "가격"), "stock": _int(args[-1], "재고")}

    if command == "계정":
        count = _int(args[0], "개수") if args else 1
        if not 1 <= count <= 20:
            raise CommandError("계정은 한 번에 1~20개까지 만들 수 있습니다.")
        return {"action": "users", "count": count}

    if command == "쿠폰":
        if len(args) not in (3, 4):
            raise CommandError("사용법: `/qa 쿠폰 <회원ID> <정액|정률> <값> [만료일수]`")
        coupon_type = COUPON_TYPE_ALIASES.get(args[1].lower())
        if coupon_type is None:
            raise CommandError(f"쿠폰 종류는 정액 또는 정률입니다: `{args[1]}`")
        return {
            "action": "coupon", "user_id": _int(args[0], "회원ID"), "discount_type": coupon_type,
            "value": _int(args[2], "할인값"), "expires_in_days": _int(args[3], "만료일수") if len(args) == 4 else 30,
        }

    if command in ("내권한", "whoami"):
        return {"action": "whoami"}

    if command in ("로그", "log"):
        limit = _int(args[0], "개수") if args else 10
        if not 1 <= limit <= 50:
            raise CommandError("로그는 1~50개까지 볼 수 있습니다.")
        return {"action": "audit", "limit": limit}

    if command == "초기화":
        # 실수로 지우지 않게 '확인'을 붙여야 실행 (웹 화면의 두 번 누르기와 같은 역할)
        if args != ["확인"]:
            raise CommandError("모든 데이터가 지워집니다. 정말 초기화하려면 `/qa 초기화 확인`")
        return {"action": "seed"}

    raise CommandError(f"알 수 없는 명령어입니다: `{command}`")


# ---------- 실행 + 메시지 ----------

def _won(n: int) -> str:
    return f"{n:,}원"


def _order_line(o: dict) -> str:
    products = ", ".join(f"{i['product_name']}×{i['quantity']}" for i in o["items"])
    shipping = "무료배송" if o["shipping_fee"] == 0 else f"배송비 {_won(o['shipping_fee'])}"
    discount = f" · 할인 {_won(o['discount_amount'])}" if o["discount_amount"] else ""
    return f"• *#{o['id']}* {STATUS_LABEL[o['status']]} · {products} · {shipping}{discount} · 결제 *{_won(o['total_amount'])}*"


def _message(text: str, public: bool = True, base_url: str | None = None) -> dict:
    """Slack 응답. 데이터를 바꾼 결과는 채널에 공개(in_channel), 도움말·에러는 본인에게만(ephemeral)."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if base_url and public:
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn", "text": f"<{base_url}/admin|판매자 어드민> · <{base_url}/shop|쇼핑몰> · <{base_url}/|QA 도구>",
        }]})
    return {"response_type": "in_channel" if public else "ephemeral", "text": text, "blocks": blocks}


def execute(conn: sqlite3.Connection, parsed: dict, actor: str = "slack") -> str:
    """해석된 명령을 실행하고 Slack 에 보여줄 본문(mrkdwn)을 돌려준다. actor 는 주문 이력에 남을 변경 주체."""
    action = parsed["action"]
    if action == "help":
        return HELP_TEXT

    if action == "orders":
        orders = qa_tools.generate_orders(
            conn, parsed["count"], parsed["scenario"], parsed["target_status"], parsed["user_id"], actor=actor,
        )
        head = (f"✅ 주문 {len(orders)}건 생성 ({SCENARIO_LABEL[parsed['scenario']]} → "
                f"{STATUS_LABEL[parsed['target_status']]}) · 회원 #{orders[0]['user_id']}")
        return "\n".join([head, *(_order_line(o) for o in orders)])

    if action == "advance":
        before = services.get_order(conn, parsed["order_id"])["status"]
        order = services.advance_to(conn, parsed["order_id"], parsed["target_status"], actor=actor)
        steps = " → ".join(STATUS_LABEL[h["to_status"]] for h in order["history"])
        return f"✅ 주문 #{order['id']} {STATUS_LABEL[before]} → *{STATUS_LABEL[order['status']]}*\n{_order_line(order)}\n이력: {steps}"

    if action == "show":
        order = services.get_order(conn, parsed["order_id"])
        history = "\n".join(
            f"   {h['changed_at'][11:19]} {STATUS_LABEL[h['to_status']]} ({h['actor']})" for h in order["history"]
        )
        return f"🔎 주문 #{order['id']} · 회원 #{order['user_id']}\n{_order_line(order)}\n{history}"

    if action == "stock":
        product = qa_tools.set_stock(conn, parsed["product_id"], parsed["stock"])
        sold_out = " (품절)" if product["stock"] == 0 else ""
        return f"✅ {product['name']} 재고 → *{product['stock']}개*{sold_out}"

    if action == "product":
        product = services.register_product(conn, parsed["name"], parsed["price"], parsed["stock"])
        return f"✅ 상품 #{product['id']} *{product['name']}* 등록 · {_won(product['price'])} · 재고 {product['stock']}개"

    if action == "users":
        users = qa_tools.bulk_users(conn, parsed["count"], prefix="slack")
        return "✅ 테스트 계정 생성\n" + "\n".join(f"• #{u['id']} {u['email']}" for u in users)

    if action == "coupon":
        coupon = services.issue_coupon(
            conn, parsed["user_id"], parsed["discount_type"], parsed["value"],
            expires_in_days=parsed["expires_in_days"],
        )
        amount = _won(coupon["value"]) if coupon["discount_type"] == "FIXED" else f"{coupon['value']}%"
        expired = " (이미 만료된 쿠폰)" if parsed["expires_in_days"] < 0 else ""
        return f"✅ 회원 #{coupon['user_id']} 쿠폰 #{coupon['id']} `{coupon['code']}` {amount} 할인{expired}"

    if action == "seed":
        products = qa_tools.seed(conn)["products"]
        return f"♻️ 모든 데이터를 지우고 기본 상품 {len(products)}종으로 초기화했습니다."

    raise CommandError(f"처리할 수 없는 명령입니다: {action}")


def _whoami(user: dict, permissions: dict) -> str:
    role = slack_auth.role_of(permissions, user["id"])
    allowed = [a for a, needed in slack_auth.REQUIRED_ROLE.items()
               if slack_auth.ROLES.index(role) >= slack_auth.ROLES.index(needed)]
    return (f"👤 Slack ID `{user['id']}` ({user.get('name') or '-'}) · 역할 *{role}* ({slack_auth.ROLE_LABEL[role]})\n"
            f"실행 가능: {', '.join(allowed)}")


def _audit_text(conn: sqlite3.Connection, limit: int) -> str:
    rows = slack_auth.recent(conn, limit)
    if not rows:
        return "기록이 없습니다."
    mark = {"ok": "✅", "denied": "🚫", "error": "❌"}
    return f"📜 최근 {len(rows)}건\n" + "\n".join(
        f"{mark.get(r['result'], '•')} {r['at'][5:16].replace('T', ' ')} {r['user_name'] or r['user_id']} `/qa {r['command']}`"
        + (f" — {r['detail']}" if r["result"] != "ok" and r["detail"] else "")
        for r in rows
    )


def handle(conn: sqlite3.Connection, text: str, base_url: str | None = None, user: dict | None = None) -> dict:
    """슬래시 커맨드 하나를 처리해 Slack 응답(dict)을 만든다.

    순서: 해석 → 권한(역할 · 채널) → 사용량 제한 → 실행 → 감사 로그. 거부 · 실패도 로그에 남고 본인에게만 보인다.
    user = {"id": Slack user_id, "name": user_name, "channel": channel_id}
    """
    user = user or {"id": "", "name": None, "channel": None}
    command = text.strip()
    try:
        parsed = parse_command(text)
    except CommandError as e:
        return _message(f"⚠️ {e}\n`/qa help` 로 사용법을 볼 수 있어요.", public=False)

    try:
        permissions = slack_auth.load_permissions()
        slack_auth.authorize(permissions, user["id"], user.get("channel") or "", parsed["action"])
        if parsed["action"] in slack_auth.RATE_LIMITED_ACTIONS:
            slack_auth.limiter.check(user["id"], slack_auth.rate_limit_per_minute())
    except slack_auth.PermissionDenied as e:
        slack_auth.record(conn, user, command, "denied", str(e))
        return _message(f"🚫 {e}", public=False)

    actor = f"slack:{user.get('name') or user['id']}"
    try:
        if parsed["action"] == "whoami":
            body = _whoami(user, permissions)
        elif parsed["action"] == "audit":
            body = _audit_text(conn, parsed["limit"])
        else:
            body = execute(conn, parsed, actor=actor)
    except DomainError as e:
        slack_auth.record(conn, user, command, "error", f"{e.code} {e.message}")
        return _message(f"❌ `{e.code}` {e.message}", public=False)

    slack_auth.record(conn, user, command, "ok")
    private = parsed["action"] in ("help", "whoami", "audit")
    return _message(body, public=not private, base_url=base_url)
