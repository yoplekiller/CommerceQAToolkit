"""Slack 슬래시 커맨드(/qa): 명령어 해석, 서명 검증, 엔드포인트 실행."""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from app.slack_bot import CommandError, parse_command, verify_signature

SECRET = "test-signing-secret"


def sign(body: bytes, timestamp: int | None = None, secret: str = SECRET) -> dict:
    """Slack 이 보내는 것과 같은 방식으로 서명 헤더를 만든다."""
    ts = str(timestamp or int(time.time()))
    digest = hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": f"v0={digest}",
            "Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def slack(client, monkeypatch):
    """서명된 /qa 요청을 보내는 헬퍼: slack("주문 3건") → 응답 JSON."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)

    def _send(text: str):
        body = urlencode({"command": "/qa", "text": text, "user_id": "U123"}).encode()
        res = client.post("/slack/commands", content=body, headers=sign(body))
        assert res.status_code == 200, res.text
        return res.json()
    return _send


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
    assert [h["actor"] for h in history[1:]] == ["slack"] * 5


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
