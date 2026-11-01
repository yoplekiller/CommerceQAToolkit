# 2026-11-01 외부 서비스 연결: 설정 파일 빈칸만 채우면 다른 서비스에 QA 도구 적용 (v5)

## 무엇을 / 왜

- 지금까지 QA 도구는 **이 레포 데모 쇼핑몰의 내부 함수**를 직접 불러서, 다른 서비스엔 쓸 수 없었다.
- 실제 회사에 가져가면 "우리 스테이징 API 주소 · 토큰 · 엔드포인트" 만 다르다. 그래서 **서비스마다 다른 부분을 전부 설정 파일(`targets/*.yaml`)로 빼고**, 주문 생성 · 상태 진행 로직은 서비스와 무관하게 만들었다.
- 결과: `targets/template.yaml` 복사 → TODO 칸 채우기 → `qatool target check` 로 점검 → `qatool target orders` 로 사용.

## 구조

```
targets/my-shop.yaml        서비스마다 다른 것: 주소 · 인증 · 엔드포인트 · 상태값 · 응답 필드 · 주문 흐름 · 무료배송 기준
        │
qa_target/config.py         읽기 · ${ENV} 치환 · TODO 빈칸 찾기 · 운영 환경 거부 · 필수 항목 검사
qa_target/adapter.py        설정대로 HTTP 호출, 응답을 표준 형식(상품 {id, price, stock, active}, 주문 {id, status, total})으로 변환
qa_target/engine.py         서비스와 무관: 시나리오별 상품 고르기 · 주문 생성 · 상태 경로(BFS) 따라 한 단계씩 진행
qa_target/check.py          점검: 빈칸 → 필수 항목 → 연결(상품 조회, 읽기만) → 필드 매핑 → 사용 가능한 기능 목록
qatool.py target ...        CLI (서버 없이 바로 대상 API 호출)
```

내장 QA 도구(`app/qa_tools.py`)의 상품 고르기도 `engine.pick_items` 를 같이 쓰도록 바꿔서 로직이 한 곳에만 있다.

## 핵심 코드 (발췌)

### 상태별로 다른 엔드포인트 — `adapter.change_status`

```python
specs = self.endpoints["change_status"]
# path 가 바로 있으면 모든 상태에 같은 엔드포인트, 아니면 상태별 엔드포인트(없으면 "*")
spec = specs if "path" in specs else (specs.get(status) or specs.get("*"))
self.call("change_status", {"order_id": order_id, "status": self.to_target_status.get(status, status)}, spec)
```

실제 서비스는 "상태 변경 API 하나" 인 곳도, "/accept · /ship · /deliver 처럼 동작별 URL" 인 곳도 있어서 둘 다 받는다.

### 본문 템플릿 — 타입 유지와 null 제거

```python
def render(template, values):
    if isinstance(template, dict):
        rendered = {k: render(v, values) for k, v in template.items()}
        return {k: v for k, v in rendered.items() if v is not None}   # 쿠폰 없는 주문이면 coupon_id 키 자체를 뺌
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template)
        if whole:
            return values.get(whole.group(1))                          # "{items}" → 리스트 그대로, "{user_id}" → 숫자 그대로
        ...
```

### 상태 변경 후 실제 상태 확인 — `engine.advance`

```python
for status in path:
    adapter.change_status(order_id, status)
order = adapter.get_order(order_id)
if order["status"] != target:
    raise ScenarioError(f"... 상태 변경 후 {order['status']} 입니다 ({target} 기대). change_status 설정 확인.")
```

설정이 틀려서 엔드포인트가 200 을 줬는데 상태가 안 바뀐 경우를 놓치지 않는다.

## 검증 포인트 (`tests/test_target_adapter.py`, 16건)

| 테스트 | 확인 |
|---|---|
| `test_template_reports_every_blank` | 템플릿 그대로 점검하면 `base_url` · `statuses.PAID` · 없는 환경변수 등을 전부 알려주고 연결은 안 함 |
| `test_production_is_refused` | `production/PROD/live/운영` 은 읽는 단계에서 거부 |
| `test_generate_orders_through_public_apis` | 데모 쇼핑몰을 설정 파일만으로: 쿠폰 주문 3건 → 배송완료, 이력이 `customer → seller → seller → carrier` (QA 도구 내부 함수가 아닌 공개 API 를 거쳤다는 증거) |
| `test_advance_to_returned_restores_stock` | 반품요청(`/return`) · 반품완료(`/status`) 처럼 상태별 다른 엔드포인트로 반품까지, 재고 원상복구 |
| `test_service_rule_errors_surface_as_target_error` | 대상 서비스가 거부한 요청(한정판 2개)은 HTTP 400 + 응답 본문(`PURCHASE_LIMIT_EXCEEDED`)이 그대로 보임 |
| `test_wrong_field_mapping_is_reported` | 응답에 없는 필드(`sale_price`)로 매핑하면 점검이 그 이름을 짚어줌 |
| `test_other_shop_orders_with_only_config` | **경로(`/api/v1/goods`) · 응답 감싸기(`result.goodsList`) · 문자열 ID(`G-1`, `O-0001`) · 상태값(`ORDER_COMPLETE` 등) · 동작별 URL** 이 전부 다른 가짜 쇼핑몰도 설정만으로 구매확정까지 |
| `test_other_shop_skips_inactive_and_maps_nested_price` | 중첩 필드(`salePrice.amount`) 매핑, 판매중지 상품 제외, 무료배송 시나리오 |
| `test_other_shop_unsupported_features_are_explained` | 반품 흐름이 없는 서비스에 반품 요청 → "갈 수 없습니다", 쿠폰 API 없음 → "issue_coupon 미설정" |
| `test_other_shop_wrong_token` | 토큰이 틀리면 점검에서 `HTTP 401` 로 바로 보임 |

전체 229건 통과. 실제 서버(uvicorn)에 `qatool target check / orders / advance` 를 돌려 같은 결과도 확인했다.

### 만들면서 고친 것
- **무료배송 시나리오가 재고 줄어든 상품을 골라 실패**: 가짜 쇼핑몰 테스트에서 발견. 양말(3,000원)이 재고 3개로 줄면 혼자 20,000원을 못 채우는데 무작위로 골라 실패했다. 재고 · 구매 제한 안에서 **혼자 기준을 채울 수 있는 상품을 우선** 고르게 바꿨다 (내장 QA 도구에도 같이 적용됨).
- YAML 에서 `{order_id}` 가 들어간 경로를 따옴표 없이 쓰면 문법 오류 → 템플릿 경로를 전부 따옴표로 감싸고 주석에 규칙 추가.

## 한계 (실무 적용 시 알아야 할 것)

- 대상 서비스에 **상태를 바꿀 수 있는 API 가 있어야** 한다. 실제 운영 API 엔 "배송완료 처리" 같은 게 택배사 연동으로만 있는 경우가 많아서, 보통 스테이징에 테스트용 관리 API 를 열거나 DB 를 직접 바꾼다. 지금은 HTTP 만 지원 (DB 어댑터는 같은 인터페이스로 추가 가능).
- 쿠폰 · 회원 생성 API 가 없으면 그 기능만 꺼진다 (회원 ID 를 직접 지정해서 사용).

## 면접 예상 질문 & 답

**Q. 이 QA 도구를 실제 회사 서비스에 바로 쓸 수 있나요?**
A. 서비스마다 다른 부분(주소 · 인증 · 엔드포인트 · 상태값 · 응답 필드 · 주문 흐름)을 설정 파일로 뺐습니다. 템플릿의 빈칸을 채우고 `target check` 로 점검하면 됩니다. 점검은 상품 조회 같은 읽기 요청만 보내서 안전하고, 운영 환경 설정은 아예 거부합니다.

**Q. 정말 다른 서비스에도 되는지 어떻게 증명했나요?**
A. 테스트 안에 경로 · 응답 구조 · 상태값 · ID 형식 · 인증 방식이 모두 다른 가짜 쇼핑몰을 만들고, 코드는 그대로 둔 채 설정만 바꿔서 주문 생성부터 구매확정까지 돌렸습니다. 데모 쇼핑몰도 내부 함수 대신 공개 API 만으로 연결해서, 이력에 고객 · 판매자 · 택배사 API 를 거친 기록이 남는 걸 확인했습니다.

**Q. 설정이 틀렸을 때 어떻게 알 수 있나요?**
A. 단계별로 알려줍니다. 빈칸이 남았으면 어느 칸인지, 토큰이 틀리면 HTTP 401, 응답 필드 이름이 다르면 어떤 매핑이 틀렸는지. 그리고 상태 변경 후 실제 상태를 다시 조회해서 기대와 다르면 "change_status 설정 확인" 으로 멈춥니다. 200 응답만 믿지 않습니다.

**Q. 왜 운영 환경을 막았나요?**
A. QA 도구는 테스트 주문을 만들고 상태를 강제로 넘깁니다. 운영에서 실행되면 실제 재고와 정산에 영향을 줍니다. 설정 파일 실수를 막기 위해 environment 값이 운영이면 읽는 단계에서 실행을 거부합니다.
