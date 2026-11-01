# Delivery QA Toolkit

배달 주문 서비스(테스트 대상 API)와, 테스트 사전조건을 명령 한 줄로 만들어주는 **QA 도구**(주문 자동생성 / 상태 자동완료 / 계정·쿠폰 발급 / 초기화).

> "배달완료된 쿠폰 주문"이 필요한 테스트를 하려면 원래는 주문 생성 → 가게 수락 → 조리 → 배달 → 완료를 손으로 넘겨야 한다.
> 이 도구로는 `python qatool.py orders gen --scenario coupon_fixed --status DELIVERED` 한 줄이면 된다.

상세 명세(상태 흐름, 도메인 규칙, API 목록): [docs/SPEC.md](docs/SPEC.md)

## 실행

```bash
pip install -r requirements.txt
uvicorn app.main:create_app --factory --reload   # http://127.0.0.1:8000/docs
```

## QA 도구 CLI

```bash
python qatool.py seed                                   # 초기화 + 기본 가게/메뉴
python qatool.py users --count 5                        # 테스트 계정 5개
python qatool.py coupon --user 1 --type FIXED --value 3000 --expires-days -1   # 만료 쿠폰
python qatool.py orders gen --count 10 --scenario coupon_percent --status DELIVERED --seed 1
python qatool.py orders advance 4 --to DELIVERING       # 주문 상태 자동완료
python qatool.py orders show 4                          # 상세 + 상태 이력
```

```
  ID        상태  가게  메뉴합계  배달비    할인  결제금액  메뉴
   1   DELIVERED     1    19,000   3,000   1,900    20,100  양념x1
   2   DELIVERED     1    21,000   3,000   2,100    21,900  양념x1, 콜라x1
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `DB_PATH` | `delivery.db` | SQLite 파일 |
| `QA_TOKEN` | `local-qa-token` | QA 도구 API `X-QA-Token` 헤더 값 |
| `QA_TOOLS_ENABLED` | `true` | `false`면 `/qa/*` 전체 404 (운영 노출 방지) |
| `QA_BASE_URL` | `http://127.0.0.1:8000` | CLI가 호출할 서버 |

## 설계 포인트

- **자동완료는 DB 덮어쓰기가 아니라 정상 전이를 한 단계씩 밟는다** → 상태 이력이 실제 흐름과 동일하게 남고(변경주체 `qa-tool`), 전이 규칙도 같이 검증된다.
- **주문 자동생성도 서비스와 같은 주문 생성 로직을 거친다** → 품절 메뉴/영업종료 가게/최소주문금액 미달 같은 "실제로는 생길 수 없는 데이터"가 만들어지지 않는다.
- `--seed`로 같은 주문 데이터를 재현할 수 있다.
- QA 도구 API는 토큰 + 기능 플래그로 보호.

## 테스트

```bash
pytest -q          # tests/test_qa_tools.py: QA 도구 자체 검증 16건
```

## 로드맵

- [x] v1: 서비스 API + QA 도구 API + CLI
- [ ] 서비스 API 자동화 테스트 스위트 (pytest, 도메인 규칙 R1~R12)
- [ ] v2: QA 도구 웹 화면
- [ ] v3: Slack 슬래시 커맨드
- [ ] Playwright / Postman 테스트가 QA 도구로 사전조건을 준비하는 구조
