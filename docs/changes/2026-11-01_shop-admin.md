# 2026-11-01 쇼핑몰(/shop) · 판매자 어드민(/admin) 화면 추가

## 무엇을 / 왜

- 이전엔 화면이 **QA 도구(`/`) 하나뿐**이었다. 고객 · 판매자 기능은 API 로만 있어서 "주문 → 발송 → 배송완료 → 반품" 흐름을 사람이 화면으로 확인할 수 없었다.
- 실제 회사 구조처럼 **고객 화면 / 판매자 어드민 / QA 도구**를 나눴다. 셋은 같은 서버 · DB 를 보므로 한 화면에서 바꾼 상태가 다른 화면에 그대로 보인다.
- 이렇게 나눠야 QA 도구의 존재 이유가 분명해진다: "어드민 반품 승인 화면을 테스트하려는데, 매번 쇼핑몰에서 주문하고 발송 · 배송완료 · 반품 신청까지 하기 번거롭다 → QA 도구로 `반품요청` 주문을 바로 만든다."

| 화면 | 기능 |
|---|---|
| 쇼핑몰 `/shop` | 이메일 간이 로그인/가입, 상품 카드(품절 · 판매중지 · 재고 N개 남음 · 구매제한 표시), 장바구니/바로구매, 쿠폰 선택, 주문서 금액(무료배송까지 남은 금액), 결제, 내 주문내역(진행 단계 표시, 취소 · 반품 신청 · 구매확정, 이력) |
| 판매자 어드민 `/admin` | 상태 탭 + 건수(처리 필요 상태는 빨간 숫자), 주문 확인(상품준비) · 발송 · 배송완료(택배사) · 반품 승인 · 판매자 취소, 체크박스 일괄 처리, 주문 상세(상품 · 결제 내역 · 처리 이력), 재고 현황 |

### 추가한 API

| API | 이유 |
|---|---|
| `POST /orders/quote` | 쇼핑몰 주문서 금액 계산. **저장은 안 하고 주문 생성과 같은 검사 · 계산**을 한다 |
| `POST /users/login`, `GET /users/{id}` | 데모용 간이 로그인(이메일만), 새로고침 후 로그인 유지 |
| `GET /users/{id}/orders` | 내 주문내역 |
| `GET /admin/orders` | 판매자 주문 목록 + 상태별 건수(탭 숫자) |

## 핵심 코드 (발췌)

### 1) 주문서 계산과 주문 생성이 같은 함수를 쓴다 — `app/services.py`

```python
def quote_order(conn, user_id, items, coupon_id=None) -> dict:
    """주문서 금액 계산. 주문 생성과 같은 검사(R1~R5, R8)를 하지만 저장은 하지 않는다."""
    ...  # 수량 합치기 → _check_orderable → 쿠폰 검증 → 배송비 · 할인 계산
    return {"items": lines, "items_amount": ..., "shipping_fee": ..., "discount_amount": ...,
            "total_amount": ..., "free_shipping_remaining": max(FREE_SHIPPING_THRESHOLD - items_amount, 0)}


def create_order(conn, user_id, items, coupon_id=None, actor="customer") -> dict:
    quote = quote_order(conn, user_id, items, coupon_id)   # ← 같은 계산을 그대로 사용
    ...  # 재고 조건부 차감, 주문 · 이력 저장, 쿠폰 사용 처리
```

기존 `create_order` 안에 있던 검사 · 계산 부분을 `quote_order` 로 떼어냈다. 동작은 그대로라 기존 API 테스트 96건이 수정 없이 통과했다.

### 2) 화면은 금액을 계산하지 않는다 — `app/static/shop.html`

```js
// 금액은 화면에서 계산하지 않고 서버 주문서 계산(/orders/quote) 결과를 그대로 보여준다
const res = await fetch("/orders/quote", { method: "POST", body: JSON.stringify({ user_id, items, coupon_id }) });
if (!res.ok) {
  // 품절 · 쿠폰 조건 미달 등은 토스트 대신 주문서 안에 표시하고 결제를 막는다
  $("quote-error").innerHTML = `<code>${data.error.code}</code> ${data.error.message}`;
  return;   // pay-button 은 disabled 유지
}
```

### 3) 어드민 일괄 처리 — `app/static/admin.html`

```js
for (const id of [...selected].sort((a, b) => a - b)) {
  try { await changeStatus(id, to, { quiet: true }); ok += 1; } catch (err) { failed.push(`#${id}`); }
}
toast(`${STATUS_LABEL[to]} 일괄 처리: 성공 ${ok}건 · 실패 ${failed.length}건 (${failed.join(", ")})`);
```

상태가 안 맞는 주문(예: 이미 배송중인데 "상품준비")은 **화면이 거르지 않고 서버가 거부**한다. 화면은 건별 결과를 모아서 한 번에 알려준다. `quiet` 옵션은 건마다 에러 토스트가 뜨지 않게 `common.js` 의 `api()` 에 추가했다.

### 4) 공통 파일 분리

세 화면이 같은 헬퍼(`api`, `toast`, `STATUS_LABEL`, 상태 배지)와 스타일을 쓰도록 `app/static/common.js`, `common.css` 로 뺐다. `/static` 은 FastAPI `StaticFiles` 로 서빙.

## 검증 포인트

| 테스트 | 무엇을 확인하나 |
|---|---|
| `tests/api/test_shop_admin_api.py::test_quote_matches_created_order_amounts` | 주문서 금액 4종(상품 · 배송비 · 할인 · 결제)이 **실제 주문 금액과 같다** — 화면에 보인 금액 = 결제 금액 |
| `test_quote_free_shipping_remaining` (parametrize) | 무료배송까지 남은 금액: 생수 1개 13,820 / 3개 1,860 / 4개 0 |
| `test_quote_does_not_change_stock_or_coupon` | 주문서 계산은 재고 · 쿠폰을 건드리지 않는다 (부작용 없음) |
| `test_user_orders_only_mine_latest_first` | 내 주문내역에 다른 회원 주문이 섞이지 않는다 |
| `test_admin_orders_filter_and_counts` | 상태 필터 + 상태별 건수 (0건 상태도 키 존재) |
| `tests/e2e/test_shop_admin.py::test_full_order_return_flow_across_shop_and_admin` | **두 탭(고객 · 판매자)을 오가며** 주문 → 상품준비 → 발송 → 배송완료 → 반품 신청 → 반품 승인, 재고가 원래대로, 고객 이력에 변경 주체(customer/seller/carrier)가 남는다 |
| `test_admin_bulk_prepare_reports_partial_failure` | 결제완료 2건 + 배송중 1건 일괄 상품준비 → "성공 2건 · 실패 1건 (#3)", 배송중 주문은 그대로 |
| `test_purchase_limit_error_shown_in_checkout` | 한정판 2개 → 주문서에 `PURCHASE_LIMIT_EXCEEDED` 표시 + 결제 버튼 비활성 |
| `test_cart_shows_free_shipping_boundary` | 11,960원 → 배송비 3,000원 + "7,840원 더 담으면 무료배송", 화장지 추가 → 무료 |

결과: 전체 149건 통과 (API 109 / E2E 17 / QA 도구 23), E2E 는 3회 연속 통과.

## 선택 이유

- **금액을 서버에서만 계산**: 화면(JS)과 서버(Python)에 배송비 규칙을 두 번 쓰면 둘이 어긋날 수 있다(예: 서버만 19,800 → 20,000 으로 바꾸고 화면은 안 바꿈). 그래서 화면은 `/orders/quote` 결과만 보여준다. 테스트도 "quote 금액 = 주문 금액" 하나로 둘의 일치를 보장한다.
- **주문서 에러는 토스트가 아니라 주문서 안에 표시**: 쿠폰을 바꾸거나 수량을 바꿀 때마다 계산하는데, 조건 미달마다 토스트가 뜨면 시끄럽다. 결제 버튼을 막는 게 더 실제 쇼핑몰에 가깝다.
- **E2E 사전조건은 QA 도구로**: 예를 들어 "배송중 주문에는 취소 버튼이 없다"를 보려고 화면에서 주문 · 발송까지 클릭하면 테스트가 느리고, 실패했을 때 원인이 흐려진다. `qa("POST", "/orders/generate", {"target_status": "SHIPPING"})` 로 만들고 **검증할 화면만 조작**한다.
- **두 탭 테스트는 같은 브라우저 컨텍스트의 새 페이지(`context.new_page()`)**: 실제로 판매자와 고객이 다른 창을 쓰는 상황과 같고, 세션 공유 문제도 없다(로그인은 localStorage 라 같은 컨텍스트면 공유되지만 어드민은 로그인 없음).
- **로케이터는 `data-testid`**, 대기는 `expect(...).to_have_text()` 자동 대기(고정 sleep 없음).

### 만들면서 고친 것

1. **로그인 박스 두 개가 동시에 보임**: `hidden` 속성을 줬는데 `.login-box { display: flex }` CSS 가 덮어써서 로그인 전/후 박스가 둘 다 보였다. `common.css` 에 `[hidden] { display: none !important; }` 를 넣어 모든 화면에서 해결. (스크린샷을 보고 발견 — 자동 테스트의 `to_be_visible` 만으론 "보이면 안 되는 것이 보이는" 건 따로 확인해야 잡힌다)
2. **로그인 사용자 표시 span 에 `id` 누락**: `data-testid` 만 있고 `id` 가 없어 JS 가 null 에 접근 → 상품 목록이 아예 안 그려졌다. Playwright 로 `pageerror` 를 잡아서 바로 원인 확인.
3. **결제 후 이력 자동 펼침 제거**: 결제 직후 이력을 자동으로 펼쳤더니, "이력 보기" 버튼이 토글이라 테스트에서 누르면 오히려 닫혔다. 화면 동작을 예측 가능하게 하려고 자동 펼침을 뺐다.
4. **테스트 로케이터 범위 버그**: `[data-testid^="admin-order-"]` 가 표 자체(`admin-order-table`)까지 잡아서 행 수가 1이 아니라 2였다. `tbody tr` 로 범위를 좁힘.

## 면접 예상 질문 & 답

**Q. 화면 금액과 실제 결제 금액이 다를 위험은 어떻게 막았나요?**
A. 금액 계산을 서버 한 곳(`quote_order`)에만 두고, 주문 생성도 이 함수를 그대로 씁니다. 화면은 계산하지 않고 결과만 보여줍니다. 그리고 "주문서 금액 = 실제 주문 금액"을 API 테스트로 고정했습니다.

**Q. 고객 화면과 판매자 화면이 연동되는 걸 어떻게 테스트했나요?**
A. Playwright 에서 같은 브라우저 컨텍스트에 페이지를 두 개 열어서(고객 탭, 판매자 탭) 번갈아 조작했습니다. 고객이 주문 → 판매자가 발송 · 배송완료 → 고객이 반품 신청 → 판매자가 승인, 마지막에 재고가 원래대로 돌아왔는지와 고객 이력에 처리 주체가 남았는지까지 확인합니다.

**Q. 일괄 처리에서 일부만 실패하면 어떻게 되나요?**
A. 건별로 서버에 요청하고, 상태가 안 맞는 주문은 서버가 409 로 거부합니다. 화면은 성공/실패 건수와 실패 주문 번호를 모아서 한 번에 보여줍니다. "결제완료 2건 + 배송중 1건" 으로 부분 실패 케이스를 E2E 로 검증했습니다.

**Q. E2E 테스트가 느려지지 않게 한 방법은?**
A. 검증 대상이 아닌 준비 단계(특정 상태의 주문 만들기 등)는 화면 대신 QA 도구 API 로 처리합니다. 화면 E2E 17건이 약 15초입니다.

**Q. 자동 테스트가 통과했는데 화면 버그가 있었던 적은?**
A. 로그인 전/후 박스가 둘 다 보이는 버그가 있었습니다. CSS `display` 가 `hidden` 속성을 덮어쓴 거라, "버튼이 보이는지"만 확인하는 테스트로는 안 잡혔고 스크린샷 확인에서 발견했습니다. 그래서 "보이면 안 되는 것이 안 보이는지"도 확인 대상이라는 걸 배웠습니다.
