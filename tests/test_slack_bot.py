"""Slack 슬래시 커맨드(/qa): 명령어 해석, 서명 검증, 엔드포인트 실행."""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from app import slack_auth
from app.slack_bot import CommandError, parse_command, verify_signature

SECRET = "test-signing-secret"


def sign(body: bytes, timestamp: int | None = None, secret: str = SECRET) -> dict:
    """Slack 이 보내는 것과 같은 방식으로 서명 헤더를 만든다."""
    ts = str(timestamp or int(time.time()))
    digest = hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": f"v0={digest}",
            "Content-Type": "application/x-www-form-urlencoded"}


# 테스트용 권한: U_ADMIN=admin, U_TESTER=tester, U_VIEWER=viewer, 그 외 none, QA 채널(C_QA)만 허용
PERMISSIONS = """
default_role: none
channels: [C_QA]
users:
  U_ADMIN: admin
  U_TESTER: tester
  U_VIEWER: viewer
"""


@pytest.fixture(autouse=True)
def slack_env(monkeypatch):
    """서명 시크릿 + 권한 설정, 사용량 제한 기록은 테스트마다 비운다."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SLACK_PERMISSIONS", PERMISSIONS)
    slack_auth.limiter.clear()


@pytest.fixture
def send(client):
    """서명된 /qa 요청: send("주문 3건", user="U_TESTER", channel="C_QA") → 응답 JSON."""
    def _send(text: str, user: str = "U_ADMIN", channel: str = "C_QA", name: str | None = None):
        body = urlencode({"command": "/qa", "text": text, "user_id": user,
                          "user_name": name or user.lower(), "channel_id": channel}).encode()
        res = client.post("/slack/commands", content=body, headers=sign(body))
        assert res.status_code == 200, res.text
        return res.json()
    return _send


@pytest.fixture
def slack(send):
    """관리자(U_ADMIN)로 QA 채널에서 보내는 헬퍼: slack("주문 3건")."""
    return lambda text: send(text)


# ---------- 명령어 해석 ----------

def test_parse_orders_with_korean_aliases_in_any_order():
    """주문 옵션은 한국어 별칭 + 순서 자유: '배송완료 정률쿠폰 3건 회원=5'."""
    assert parse_command("주문 배송완료 정률쿠폰 3건 회원=5") == {
        "action": "orders", "count": 3, "scenario": "coupon_percent", "target_status": "DELIVERED", "user_id": 5,
    }


def test_parse_orders_defaults():
    """옵션 없이 '주문'만 쓰면 기본 시나리오 1건, 결제완료."""
    assert parse_command("주문") == {
        "action": "orders", "count": 1, "scenario": "basic", "target_status": "PAID", "user_id": None,
    }


@pytest.mark.parametrize("count, ok", [(0, False), (1, True), (20, True), (21, False)])
def test_parse_orders_count_boundary(count, ok):
    """주문 건수는 1~20건 (경계값)."""
    if ok:
        assert parse_command(f"주문 {count}건")["count"] == count
    else:
        with pytest.raises(CommandError):
            parse_command(f"주문 {count}건")


@pytest.mark.parametrize("text, expected", [
    ("상태 12 반품완료", {"action": "advance", "order_id": 12, "target_status": "RETURNED"}),
    ("상태 3 shipping", {"action": "advance", "order_id": 3, "target_status": "SHIPPING"}),
    ("조회 7", {"action": "show", "order_id": 7}),
    ("재고 4 0", {"action": "stock", "product_id": 4, "stock": 0}),
    ("계정", {"action": "users", "count": 1}),
    ("쿠폰 5 정률 10 -1", {"action": "coupon", "user_id": 5, "discount_type": "PERCENT", "value": 10, "expires_in_days": -1}),
    ("초기화 확인", {"action": "seed"}),
    ("상품 테스트 머그컵 19799 5", {"action": "product", "name": "테스트 머그컵", "price": 19799, "stock": 5}),
    ("", {"action": "help"}),
    ("help", {"action": "help"}),
])
def test_parse_other_commands(text, expected):
    """상태/조회/재고/계정/쿠폰/초기화/도움말 해석."""
    assert parse_command(text) == expected


@pytest.mark.parametrize("text, message", [
    ("주문 아무거나", "알 수 없는 옵션"),
    ("상태 12", "사용법"),
    ("상태 abc 배송중", "숫자여야"),
    ("상태 12 날아감", "알 수 없는 상태"),
    ("쿠폰 5 반값 10", "정액 또는 정률"),
    ("초기화", "초기화 확인"),
    ("상품 머그컵 1000", "사용법"),
    ("상품 머그컵 천원 5", "숫자여야"),
    ("삭제 1", "알 수 없는 명령어"),
])
def test_parse_errors(text, message):
    """잘못된 입력은 무엇이 틀렸는지 알려준다. 초기화는 '확인' 없이는 실행 안 됨."""
    with pytest.raises(CommandError, match=message):
        parse_command(text)


# ---------- 서명 검증 ----------

def test_signature_valid_and_tampered():
    """올바른 서명은 통과, 본문이 바뀌거나 시크릿이 다르면 실패."""
    body = b"text=%EC%A3%BC%EB%AC%B8"
    headers = sign(body)
    ts, sig = headers["X-Slack-Request-Timestamp"], headers["X-Slack-Signature"]
    assert verify_signature(SECRET, ts, body, sig)
    assert not verify_signature(SECRET, ts, body + b"x", sig)
    assert not verify_signature("other-secret", ts, body, sig)


@pytest.mark.parametrize("age, ok", [(299, True), (301, False)])
def test_signature_rejects_old_requests(age, ok):
    """5분(300초)보다 오래된 요청은 재전송 공격으로 보고 거부 (경계값)."""
    now = 1_700_000_000
    body = b"text=x"
    headers = sign(body, timestamp=now - age)
    assert verify_signature(SECRET, headers["X-Slack-Request-Timestamp"], body,
                            headers["X-Slack-Signature"], now=now) is ok


# ---------- 엔드포인트 ----------

def test_endpoint_hidden_without_secret(client, monkeypatch):
    """SLACK_SIGNING_SECRET 이 없으면 기능이 꺼져 있어 404."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    assert client.post("/slack/commands", content=b"text=help").status_code == 404


def test_endpoint_rejects_bad_signature(client, monkeypatch):
    """서명이 틀리면 401 이고 아무것도 실행되지 않는다."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    body = urlencode({"text": "초기화 확인"}).encode()
    headers = sign(body, secret="attacker")
    assert client.post("/slack/commands", content=body, headers=headers).status_code == 401


def test_orders_command_creates_orders_in_channel(client, slack):
    """'/qa 주문 3건 무료배송 배송완료' → 주문 3건이 실제로 생기고, 결과는 채널에 공개된다."""
    res = slack("주문 3건 무료배송 배송완료")
    assert res["response_type"] == "in_channel"
    assert "주문 3건 생성 (무료배송 → 배송완료)" in res["text"]
    orders = client.get("/admin/orders").json()["orders"]
    assert len(orders) == 3
    assert all(o["status"] == "DELIVERED" and o["shipping_fee"] == 0 for o in orders)


def test_advance_command_records_slack_actor(client, slack):
    """'/qa 상태 1 반품완료' → 정상 단계를 밟고, 이력 변경 주체가 slack 으로 남는다."""
    slack("주문 1건 결제완료")
    res = slack("상태 1 반품완료")
    assert "결제완료 → *반품완료*" in res["text"]
    history = client.get("/orders/1").json()["history"]
    assert [h["actor"] for h in history[1:]] == ["slack:u_admin"] * 5


def test_domain_error_is_private_message(slack):
    """서비스 규칙 위반(배송중 → 취소)은 에러 코드와 함께 본인에게만(ephemeral) 보인다."""
    slack("주문 1건 배송중")
    res = slack("상태 1 취소")
    assert res["response_type"] == "ephemeral"
    assert "INVALID_STATUS_TRANSITION" in res["text"]


def test_stock_and_coupon_commands(client, slack):
    """재고 0 → 품절 표시, 만료일 음수 쿠폰 → 만료 쿠폰으로 발급."""
    assert "(품절)" in slack("재고 4 0")["text"]
    assert client.get("/products/4").json()["stock"] == 0
    user_id = int(slack("계정")["text"].split("#")[1].split()[0])
    assert "이미 만료된 쿠폰" in slack(f"쿠폰 {user_id} 정액 3000 -1")["text"]


def test_reset_requires_confirm(client, slack):
    """'/qa 초기화'만 쓰면 실행되지 않고, '초기화 확인'이어야 데이터가 지워진다."""
    slack("주문 2건")
    assert slack("초기화")["response_type"] == "ephemeral"
    assert len(client.get("/admin/orders").json()["orders"]) == 2
    slack("초기화 확인")
    assert client.get("/admin/orders").json()["orders"] == []


def test_help_is_private(slack):
    """도움말은 본인에게만."""
    res = slack("help")
    assert res["response_type"] == "ephemeral"
    assert "/qa 주문" in res["text"]


def test_links_use_public_base_url(slack, monkeypatch):
    """결과 메시지 하단 링크는 PUBLIC_BASE_URL 기준 (Render 프록시 뒤에서도 https 주소)."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.onrender.com")
    res = slack("주문")
    assert "https://example.onrender.com/admin" in str(res["blocks"])


def test_product_command_registers_with_seller_rules(client, slack):
    """'/qa 상품' 은 판매자 등록과 같은 검사(R16): 정상 등록은 채널 공개, 0원은 INVALID_PRODUCT 비공개."""
    res = slack("상품 테스트 머그컵 19799 5")
    assert "상품 #9 *테스트 머그컵* 등록" in res["text"]
    assert client.get("/products/9").json()["price"] == 19799
    bad = slack("상품 공짜 0 1")
    assert bad["response_type"] == "ephemeral" and "INVALID_PRODUCT" in bad["text"]


# ---------- 권한 · 사용량 제한 · 감사 로그 ----------

@pytest.mark.parametrize("user, command, allowed", [
    ("U_STRANGER", "help", True), ("U_STRANGER", "내권한", True), ("U_STRANGER", "조회 1", False),
    ("U_VIEWER", "조회 1", True), ("U_VIEWER", "주문", False),
    ("U_TESTER", "주문", True), ("U_TESTER", "재고 4 0", True), ("U_TESTER", "초기화 확인", False),
    ("U_TESTER", "로그", False), ("U_ADMIN", "초기화 확인", True), ("U_ADMIN", "로그", True),
])
def test_role_matrix(send, user, command, allowed):
    """역할별 실행 가능 명령: none(help·내권한) < viewer(+조회) < tester(+데이터 생성/변경) < admin(+초기화·로그)."""
    send("주문", user="U_ADMIN")  # 조회 대상 주문 1건
    res = send(command, user=user)
    assert ("🚫" not in res["text"]) is allowed, res["text"]


def test_denied_message_tells_required_role_and_user_id(send):
    """권한이 없으면 필요한 역할 · 현재 역할 · 등록할 사용자 ID 를 본인에게만 알려주고, 데이터는 안 바뀐다."""
    res = send("주문 3건", user="U_VIEWER")
    assert res["response_type"] == "ephemeral"
    assert "`tester` 권한이 필요합니다" in res["text"] and "`viewer`" in res["text"] and "`U_VIEWER`" in res["text"]


def test_channel_restriction(send, client):
    """허용 채널 밖에서는 관리자도 데이터 명령을 못 쓴다 (help · 내권한은 어디서나)."""
    assert "이 채널에서는" in send("주문", channel="C_RANDOM")["text"]
    assert client.get("/admin/orders").json()["orders"] == []
    assert "🚫" not in send("내권한", channel="C_RANDOM")["text"]


def test_no_permission_config_locks_everything(send, monkeypatch, tmp_path):
    """권한 설정이 아예 없으면 전부 잠긴다 (등록 전 기본값은 '닫힘')."""
    monkeypatch.delenv("SLACK_PERMISSIONS")
    monkeypatch.setenv("SLACK_PERMISSIONS_FILE", str(tmp_path / "없음.yaml"))
    assert "🚫" in send("주문")["text"]
    assert "역할 *none*" in send("내권한")["text"]


def test_whoami_shows_id_and_role(send):
    """내권한: 내 Slack ID 와 역할, 실행 가능한 명령 목록 (등록 요청할 때 ID 를 알려주는 용도)."""
    res = send("내권한", user="U_TESTER", name="jaemin")
    assert "`U_TESTER`" in res["text"] and "역할 *tester*" in res["text"]
    assert res["response_type"] == "ephemeral"


def test_rate_limit_per_user(send, monkeypatch):
    """한 사람이 1분에 N번 넘게 데이터를 바꾸면 막힌다. 다른 사람과 조회 명령은 영향 없음."""
    monkeypatch.setenv("SLACK_RATE_LIMIT_PER_MINUTE", "2")
    assert "🚫" not in send("주문", user="U_TESTER")["text"]
    assert "🚫" not in send("계정", user="U_TESTER")["text"]
    assert "1분에 2번까지" in send("계정", user="U_TESTER")["text"]
    assert "🚫" not in send("계정", user="U_ADMIN")["text"]
    assert "🔎 주문 #1" in send("조회 1", user="U_TESTER")["text"]  # 조회는 제한 대상 아님


def test_rate_limiter_window():
    """제한은 최근 60초 기준: 60초가 지나면 다시 허용된다 (경계값)."""
    limiter = slack_auth.RateLimiter()
    limiter.check("U1", 1, now=1000.0)
    with pytest.raises(slack_auth.PermissionDenied):
        limiter.check("U1", 1, now=1059.9)
    limiter.check("U1", 1, now=1060.0)


def test_audit_log_records_ok_denied_error_and_survives_reset(send):
    """모든 명령이 감사 로그에 남는다(성공 · 거부 · 실패). 데이터 초기화 후에도 로그는 남는다."""
    send("주문", user="U_TESTER")
    send("초기화 확인", user="U_TESTER")      # 거부
    send("상태 999 배송중", user="U_TESTER")   # 없는 주문 → 실패
    send("초기화 확인", user="U_ADMIN")       # 성공 (데이터 초기화)
    log = send("로그 10", user="U_ADMIN")["text"]
    assert log.count("✅") == 2 and "🚫" in log and "❌" in log and "ORDER_NOT_FOUND" in log


def test_order_history_records_who(send, client):
    """주문 이력에 Slack 사용자 이름이 남는다 (slack:이름)."""
    send("주문 배송중", user="U_TESTER", name="jaemin")
    assert {h["actor"] for h in client.get("/orders/1").json()["history"]} == {"slack:jaemin"}


def test_invalid_role_in_config(monkeypatch):
    """권한 설정에 없는 역할 이름이 있으면 바로 알려준다 (오타 방지)."""
    monkeypatch.setenv("SLACK_PERMISSIONS", "users: {U1: superuser}")
    with pytest.raises(ValueError, match="superuser"):
        slack_auth.load_permissions()
