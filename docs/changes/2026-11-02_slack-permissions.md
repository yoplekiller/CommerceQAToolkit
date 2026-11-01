# 2026-11-02 Slack /qa 권한 · 감사 로그 · 사용량 제한 (v4.1)

## 무엇을 / 왜

- v4 의 서명 검증은 **"이 요청이 Slack 에서 왔는지"** 만 확인했다. **"누가 보냈는지"** 는 안 봐서, 앱이 설치된 워크스페이스의 누구나 `/qa 초기화 확인` 까지 실행할 수 있었고 이력엔 `slack` 으로만 남아 누가 했는지 몰랐다.
- 여러 사람이 쓰는 실무 QA 봇에 필요한 세 가지를 붙였다:
  1. **권한**: 역할(none < viewer < tester < admin) + 허용 채널. 설정이 없으면 **전부 잠김**
  2. **감사 로그**: 모든 명령(성공 · 거부 · 실패)을 기록, 데이터 초기화에도 유지. 주문 이력엔 `slack:이름`
  3. **사용량 제한**: 사람별 1분 10회 (데이터 변경 명령만)

| 역할 | 명령 |
|---|---|
| none (미등록) | help, 내권한 |
| viewer | + 조회 |
| tester | + 주문 · 상태 · 재고 · 계정 · 쿠폰 · 상품 |
| admin | + 초기화 · 로그 |

새 명령: `/qa 내권한`(내 Slack ID · 역할 — 등록 요청할 때 ID 확인용), `/qa 로그 [N]`(admin).

## 처리 순서 — `app/slack_bot.handle`

```
서명 검증(라우터) → 명령 해석 → 권한(채널 · 역할) → 사용량 제한 → 실행 → 감사 로그
                                   └ 거부되면 이유를 본인에게만 + 로그에 denied
```

```python
try:
    permissions = slack_auth.load_permissions()
    slack_auth.authorize(permissions, user["id"], user.get("channel") or "", parsed["action"])
    if parsed["action"] in slack_auth.RATE_LIMITED_ACTIONS:
        slack_auth.limiter.check(user["id"], slack_auth.rate_limit_per_minute())
except slack_auth.PermissionDenied as e:
    slack_auth.record(conn, user, command, "denied", str(e))
    return _message(f"🚫 {e}", public=False)
```

- 사용자 정보는 Slack 이 요청 본문에 넣어 보내는 `user_id` · `user_name` · `channel_id`. 이 본문은 서명으로 보호되므로 중간에 바꿀 수 없다 (서명 검증이 먼저 있어야 권한 판단을 믿을 수 있는 이유).
- 권한 설정은 환경변수 `SLACK_PERMISSIONS`(YAML) → `SLACK_PERMISSIONS_FILE` → `slack/permissions.yaml` 순. 환경변수 우선이라 **권한을 바꿀 때 커밋 · 코드 수정 없이** 대시보드에서 바꾸면 된다.

## 설계 선택

- **기본값 "잠김"**: 설정을 깜빡하면 아무도 못 쓰는 게, 누구나 쓰는 것보다 안전하다. 대신 `내권한` 은 항상 열어서 등록에 필요한 ID 를 스스로 확인하게 했다.
- **채널 제한은 역할보다 먼저**: 관리자라도 아무 채널에서나 테스트 데이터를 만들면 결과 메시지(채널 공개)가 엉뚱한 곳에 뿌려진다.
- **감사 로그는 초기화 대상에서 제외**: `reset_all` 의 테이블 목록에 넣지 않았다. "누가 초기화했는지" 가 초기화로 지워지면 안 되니까.
- **사용량 제한은 메모리 기반**: 무료 서버 한 대라 충분. 서버가 여러 대면 Redis 같은 공유 저장소로 옮겨야 한다 (한계로 기록).
- **역할 이름 오타는 즉시 에러**: `superuser` 처럼 없는 역할을 쓰면 조용히 none 이 되는 대신 설정 로드에서 에러.

## 검증 포인트 (`tests/test_slack_bot.py`, 권한 관련 21건 추가 → 58건)

| 테스트 | 확인 |
|---|---|
| `test_role_matrix` (11케이스) | 미등록 · viewer · tester · admin 각각 허용/거부 경계 (예: tester 는 주문 ✓ 초기화 ✗) |
| `test_denied_message_tells_required_role_and_user_id` | 거부 시 필요한 역할 · 현재 역할 · 등록할 ID 안내, 본인에게만, 데이터 안 바뀜 |
| `test_channel_restriction` | 허용 채널 밖에선 admin 도 데이터 명령 불가, help · 내권한은 가능 |
| `test_no_permission_config_locks_everything` | 설정 없음 → 전부 잠김 |
| `test_rate_limit_per_user` | 2회 제한에서 3번째 거부, 다른 사람 · 조회는 영향 없음 |
| `test_rate_limiter_window` | 59.9초 거부 / 60초 허용 (경계값) |
| `test_audit_log_records_ok_denied_error_and_survives_reset` | 성공 2 · 거부 1 · 실패 1(ORDER_NOT_FOUND) 기록, admin 초기화 후에도 로그 유지 |
| `test_order_history_records_who` | 주문 이력 변경 주체가 `slack:jaemin` |
| `test_invalid_role_in_config` | 없는 역할 이름은 설정 로드 에러 |

기존 Slack 테스트는 관리자 사용자로 보내도록 픽스처만 바꿨고, 이력 주체 기대값을 `slack` → `slack:u_admin` 으로 수정. 전체 249건 통과.

### 같이 고친 것
- 지난 커밋(상품 관리)에서 `/qa help` 에 `/qa 상품` 줄을 넣으려던 문자열 치환이 **조용히 실패**해서 도움말에 빠져 있었다. 이번에 도움말에 추가. (교훈: 문자열 치환으로 코드를 고친 뒤엔 결과를 grep 으로 확인)

## 한계

- 웹 QA 도구(`/`)는 여전히 토큰 하나로 보호 (사람별 로그인 · 권한 없음). 실무라면 사내 SSO 뒤에 두는 게 일반적.
- Slack 사용자 그룹(@qa-team) 단위 권한은 아직 없음 — 사용자 ID 단위.
- 사용량 제한 · 권한 캐시가 서버 메모리라 여러 대로 늘리면 공유 저장소 필요.

## 면접 예상 질문 & 답

**Q. 서명 검증이 있는데 권한이 또 필요한 이유는?**
A. 서명은 "요청이 Slack 에서 왔다" 는 인증이고, 권한은 "그 사람이 이걸 해도 되는가" 의 인가입니다. 서명만 있으면 워크스페이스의 누구나 초기화를 할 수 있었습니다. Slack 이 서명된 본문에 넣어주는 user_id · channel_id 로 역할과 채널을 확인합니다. 본문이 서명으로 보호되기 때문에 이 값을 믿을 수 있습니다.

**Q. 권한 설정이 없을 때 기본 동작은?**
A. 전부 잠급니다(도움말과 내권한만). 설정 실수로 누구나 쓰게 되는 것보다 아무도 못 쓰는 게 안전하다고 판단했습니다. 대신 `내권한` 으로 자기 ID 를 확인해서 관리자에게 등록을 요청하는 흐름을 만들었습니다.

**Q. 누가 테스트 데이터를 지웠는지 어떻게 아나요?**
A. 모든 명령을 감사 로그 테이블에 성공 · 거부 · 실패로 남기고, 이 테이블은 데이터 초기화 대상에서 뺐습니다. 주문 이력에도 `slack:이름` 으로 실행자가 남습니다. 관리자는 `/qa 로그` 로 바로 볼 수 있습니다.

**Q. 권한 경계를 어떻게 테스트했나요?**
A. 역할 4개 × 대표 명령을 표 하나(parametrize 11케이스)로 만들어 허용/거부를 검증했고, 채널 제한, 설정 없음, 사용량 제한 60초 경계, 감사 로그 기록 · 보존을 각각 테스트했습니다.
