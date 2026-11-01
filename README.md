# Commerce QA Toolkit

오픈마켓형 커머스 주문 서비스(테스트 대상 API)와, 테스트 사전조건을 명령 한 줄로 만들어주는 **QA 도구**
(주문 자동생성 / 상태 자동완료 / 장바구니 자동 담기 / 재고 조정 / 계정·쿠폰 발급 / 초기화).

> "배송완료된 쿠폰 주문으로 반품 테스트"를 하려면 원래는 장바구니 담기 → 결제 → 판매자 발송 → 배송완료까지 손으로 넘겨야 한다.
> 이 도구로는 `python qatool.py orders gen --scenario coupon_fixed --status DELIVERED` 한 줄이면 된다.

상세 명세(상태 흐름, 도메인 규칙 R1~R15, API 목록, 시드 데이터): [docs/SPEC.md](docs/SPEC.md)

## 실행

```bash
pip install -r requirements.txt
uvicorn app.main:create_app --factory --reload   # http://127.0.0.1:8000/docs
```

## QA 도구 CLI

```bash
python qatool.py seed                                   # 초기화 + 기본 상품 8종
python qatool.py users --count 5                        # 테스트 계정 5개
python qatool.py coupon --user 1 --type FIXED --value 3000 --expires-days -1   # 만료 쿠폰
python qatool.py stock 4 0                              # 4번 상품 품절 만들기
python qatool.py cart --user 1 --count 3                # 장바구니 자동 담기
python qatool.py orders gen --count 3 --scenario coupon_percent --status DELIVERED --seed 1
python qatool.py orders advance 1 --to RETURNED         # 주문 상태 자동완료
python qatool.py orders show 1                          # 상세 + 상태 이력
```

```
  ID              상태  상품합계  배송비    할인  결제금액  상품
   1         DELIVERED     5,980   3,000     598     8,382  생수 2L x 6(1개)
   2         DELIVERED    19,800       0   1,980    17,820  화장지 30롤(1개)
   3         DELIVERED    89,000       0   5,000    84,000  무선 이어폰(1개)

$ python qatool.py orders advance 1 --to RETURNED
이력: PAID -> PREPARING -> SHIPPING -> DELIVERED -> RETURN_REQUESTED -> RETURNED
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `DB_PATH` | `commerce.db` | SQLite 파일 |
| `QA_TOKEN` | `local-qa-token` | QA 도구 API `X-QA-Token` 헤더 값 |
| `QA_TOOLS_ENABLED` | `true` | `false`면 `/qa/*` 전체 404 (운영 노출 방지) |
| `QA_BASE_URL` | `http://127.0.0.1:8000` | CLI가 호출할 서버 |

## 설계 포인트

- **자동완료는 DB 덮어쓰기가 아니라 허용된 전이를 한 단계씩 밟는다** (목표 상태까지 최단 경로를 BFS로 계산) → 상태 이력이 실제 흐름과 동일하게 남고, 취소/반품 시 재고·쿠폰 복구도 실제와 똑같이 일어난다.
- **주문 자동생성/장바구니 담기도 서비스와 같은 로직을 거친다** → 품절·판매중지·구매제한 초과 같은 "실제로는 생길 수 없는 데이터"가 만들어지지 않는다.
- 재고 차감은 `UPDATE ... WHERE stock >= ?` 조건부 갱신으로 초과 판매를 막는다.
- `--seed`로 같은 주문 데이터를 재현할 수 있다.
- QA 도구 API는 토큰 + 기능 플래그로 보호.

## 테스트

```bash
pytest -q          # tests/test_qa_tools.py: QA 도구 자체 검증 21건
```

## 로드맵

- [x] v1: 서비스 API + QA 도구 API + CLI
- [ ] 서비스 API 자동화 테스트 스위트 (pytest, 도메인 규칙 R1~R15)
- [ ] v2: QA 도구 웹 화면
- [ ] v3: Slack 슬래시 커맨드
- [ ] Playwright / Postman 테스트가 QA 도구로 사전조건을 준비하는 구조
