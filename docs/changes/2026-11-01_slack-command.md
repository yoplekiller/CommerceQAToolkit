# 2026-11-01 Slack 슬래시 커맨드 `/qa` (v4)

## 무엇을 / 왜

- QA 도구를 **Slack 에서 바로** 쓰게 했다. `/qa 주문 3건 정률쿠폰 배송완료` 한 줄이면 주문이 만들어지고 배송완료까지 진행된다.
- 실무 QA 팀은 테스트 데이터 준비를 메신저 봇으로 하는 경우가 많다. 웹 화면을 따로 열 필요가 없고, **결과가 채널에 공개**돼서 "누가 어떤 테스트 데이터를 만들었는지" 팀이 같이 본다.
- 새 기능을 만든 게 아니라 **QA 도구와 같은 함수(`qa_tools` / `services`)를 Slack 이라는 입구로 하나 더 연 것**이다. 그래서 웹 화면 · CLI · Slack 어디서 만들든 데이터 규칙이 같다.

| 명령 | 하는 일 |
|---|---|
| `/qa 주문 [N건] [시나리오] [상태] [회원=ID]` | 주문 자동생성 + 목표 상태까지 진행 (옵션 순서 자유) |
| `/qa 상태 <주문ID> <상태>` | 상태 자동완료, 이력 주체 `slack` |
| `/qa 조회 <주문ID>` | 주문 상세 + 처리 이력 |
| `/qa 재고 <상품ID> <수량>` | 재고 설정 (0 = 품절) |
| `/qa 계정 [N]` | 테스트 계정 생성 |
| `/qa 쿠폰 <회원ID> <정액\|정률> <값> [만료일수]` | 쿠폰 발급 (음수 = 만료 쿠폰) |
| `/qa 초기화 확인` | 기본 데이터로 초기화 (`확인` 필수) |

## 핵심 코드 (발췌)

### 1) 해석 → 실행 → 메시지, 세 단계로 나눔 — `app/slack_bot.py`

```python
def handle(conn, text, base_url=None) -> dict:
    try:
        parsed = parse_command(text)          # "주문 3건 정률쿠폰 배송완료" → {"action": "orders", "count": 3, ...}
    except CommandError as e:
        return _message(f"⚠️ {e}\n`/qa help` 로 사용법을 볼 수 있어요.", public=False)
    try:
        body = execute(conn, parsed)          # qa_tools.generate_orders / services.advance_to ...
    except DomainError as e:
        return _message(f"❌ `{e.code}` {e.message}", public=False)
    return _message(body, public=parsed["action"] != "help", base_url=base_url)
```

나눈 이유: `parse_command` 는 순수 함수라 Slack 없이 입력 → 결과만 비교하면 된다. 그래서 별칭 · 경계값 · 잘못된 입력을 parametrize 로 빠르게 많이 검증할 수 있다.

### 2) 요청 서명 검증

```python
def verify_signature(signing_secret, timestamp, body, signature, now=None) -> bool:
    if abs((now or time.time()) - int(timestamp)) > SIGNATURE_MAX_AGE_SECONDS:   # 5분
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
```

- 이 엔드포인트는 토큰 없이 인터넷에 열려 있다. 서명이 없으면 누구나 `/slack/commands` 로 `초기화 확인` 을 보낼 수 있다.
- 서명은 **받은 바이트 그대로** 계산해야 해서 라우터에서 폼을 파싱하기 전에 `await request.body()` 로 원본을 읽는다.
- `hmac.compare_digest` 는 비교 시간이 일정해서 한 글자씩 맞춰보는 타이밍 공격을 막는다.
- 5분 제한은 정상 요청을 가로채 나중에 다시 보내는 재전송 공격 방지.

### 3) 공개 범위

| 결과 | response_type | 이유 |
|---|---|---|
| 주문 생성 · 상태 이동 · 재고 · 쿠폰 · 초기화 | `in_channel` (채널 공개) | 공유 테스트 데이터가 바뀐 걸 팀이 알아야 함 |
| 도움말 · 입력 오류 · 규칙 위반 | `ephemeral` (본인만) | 채널을 어지럽히지 않음 |

## 검증 포인트 (`tests/test_slack_bot.py`, 34건)

| 테스트 | 확인하는 것 |
|---|---|
| `test_parse_orders_with_korean_aliases_in_any_order` | `배송완료 정률쿠폰 3건 회원=5` 처럼 순서가 섞여도 같은 결과 |
| `test_parse_orders_count_boundary` | 건수 0 ✗ / 1 ✓ / 20 ✓ / 21 ✗ |
| `test_parse_errors` | 잘못된 옵션 · 인자 부족 · 숫자 아님 · 없는 상태 · 쿠폰 종류 오류 · `초기화` 단독 · 없는 명령 → 무엇이 틀렸는지 메시지 |
| `test_signature_valid_and_tampered` | 정상 ✓, 본문 1바이트 변조 ✗, 다른 시크릿 ✗ |
| `test_signature_rejects_old_requests` | 299초 ✓ / 301초 ✗ (5분 경계) |
| `test_endpoint_hidden_without_secret` | 시크릿 미설정 → 404 |
| `test_endpoint_rejects_bad_signature` | 틀린 서명으로 `초기화 확인` → 401, 아무것도 실행 안 됨 |
| `test_orders_command_creates_orders_in_channel` | 실제로 주문 3건이 DB 에 생기고 전부 배송완료 · 무료배송 |
| `test_advance_command_records_slack_actor` | 결제완료 → 반품완료: 이력 5단계 모두 주체 `slack` |
| `test_domain_error_is_private_message` | 배송중 → 취소는 `INVALID_STATUS_TRANSITION`, 본인에게만 |
| `test_reset_requires_confirm` | `초기화` 만으론 데이터 그대로, `초기화 확인` 이어야 삭제 |

테스트는 **Slack 이 보내는 것과 같은 방식으로 서명한 요청**(`sign()` 헬퍼)을 보내서 실제 엔드포인트를 통과시킨다. 전체 183건 통과.

## 선택 이유

- **명령어 하나(`/qa`) + 하위 명령**: Slack 앱마다 슬래시 커맨드를 여러 개 등록하면 이름 충돌 · 관리가 번거롭다. 하나로 두고 도움말을 붙였다.
- **한국어 별칭 + 영어 원문 둘 다**: 팀원은 `배송완료`, 자동화 스크립트는 `DELIVERED` 를 쓸 수 있게.
- **동기 처리**: Slack 은 3초 안에 응답을 받아야 한다. 이 서비스는 SQLite 라 명령 하나가 수십 ms 라 바로 응답한다. 느린 작업이 생기면 즉시 "처리 중" 응답 후 `response_url` 로 결과를 보내는 비동기 방식으로 바꾸면 된다.
- **매니페스트(`slack/manifest.yml`)**: 앱 설정(명령어 이름 · URL · 권한)을 코드로 남겨 클릭 대신 붙여넣기로 만들고, 설정이 레포에 기록되게.
- **한도**: 주문은 한 번에 최대 20건, 계정 20개 — 채널 메시지가 너무 길어지거나 공개 데모 DB 가 불어나지 않게.

## 알려진 제약

- 무료 Render 가 잠들어 있으면 깨어나는 데 ~30초 → Slack 3초 제한에 걸려 첫 명령이 `dispatch_failed` 일 수 있다. 데모 주소를 열어 깨운 뒤 사용.
- Slack 사용자 ↔ 회원 매핑은 없다 (`회원=ID` 로 지정, 없으면 테스트 계정을 새로 만든다).

## 면접 예상 질문 & 답

**Q. 슬래시 커맨드 엔드포인트는 공개 URL 인데 보안은 어떻게 했나요?**
A. Slack 이 모든 요청에 붙이는 서명(Signing Secret 으로 만든 HMAC-SHA256)을 검증합니다. 본문이 1바이트만 바뀌어도, 시크릿이 달라도 401 이고, 5분 지난 요청은 재전송 공격으로 보고 거부합니다. 시크릿이 설정되지 않은 환경에선 엔드포인트 자체가 404 입니다. 이 경우들을 전부 테스트로 고정했습니다.

**Q. Slack 연동을 Slack 없이 어떻게 테스트했나요?**
A. 명령어 해석을 순수 함수로 분리해 입력 문자열 → 결과 dict 를 parametrize 로 검증했고, 엔드포인트는 테스트에서 Slack 과 같은 방식으로 서명한 요청을 직접 만들어 보냈습니다. 그래서 실제 Slack 워크스페이스 없이도 CI 에서 돕니다.

**Q. 웹 화면 · CLI · Slack 에서 같은 기능을 하는데 동작이 달라질 위험은?**
A. 세 입구 모두 같은 `qa_tools` / `services` 함수를 부릅니다. 입구는 입력을 해석하고 결과를 보여주는 일만 합니다. 그래서 규칙은 한 곳에만 있고, 이력의 변경 주체(`qa-tool` / `slack`)로 어디서 바꿨는지만 구분됩니다.

**Q. Slack 3초 응답 제한은 어떻게 다뤘나요?**
A. 지금 명령은 SQLite 에서 수십 ms 라 동기로 응답합니다. 오래 걸리는 작업이 생기면 즉시 "처리 중" 으로 응답하고 결과는 `response_url` 로 나중에 보내는 방식으로 바꿀 계획입니다. 무료 호스팅이 잠들어 있을 때 첫 요청이 늦는 건 알려진 제약으로 문서에 적었습니다.
