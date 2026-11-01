# 2026-11-01 판매자 상품 관리 (상품 등록 · 가격/재고/구매 제한 수정 · 판매중지) + R16

## 무엇을 / 왜

- 이전엔 재고를 **QA 도구(테스터용)** 로만 바꿀 수 있었고, 판매자 어드민은 재고를 보기만 했다. 실제 쇼핑몰에선 판매자가 상품을 등록하고 재고를 입고한다.
- 판매자 어드민에 **상품 등록 / 가격 · 재고 · 1회 구매 제한 수정 / 판매중지 · 재개** 를 넣고, 입력 검사 규칙을 **R16** 으로 SPEC 에 추가했다.
- Slack `/qa 상품 <이름> <가격> <재고>` 도 추가 (판매자 등록과 같은 검사).

| API | 설명 |
|---|---|
| `POST /admin/products` | 상품 등록 |
| `PATCH /admin/products/{id}` | 보낸 항목만 수정. `max_per_order: null` = 구매 제한 해제 |

### QA 도구의 상품 생성과 뭐가 다른가

| | 판매자 등록 (`/admin/products`) | QA 도구 (`/qa/products`) |
|---|---|---|
| 목적 | 실제 판매자 기능 → **테스트 대상** | 경계값 테스트용 데이터 준비 |
| 검사 | R16 (가격 1원 이상 등) | 최소한만 (가격 0원도 허용 — 일부러 이상한 데이터를 만들 수 있어야 해서) |

## 핵심 코드 — `app/services.py`

```python
def update_product(conn, product_id, changes: dict) -> dict:
    """changes 에 들어온 항목만 바꾼다. max_per_order=None 은 구매 제한 해제."""
    _validate_product_fields(changes.get("name"), changes.get("price"), changes.get("stock"), changes.get("max_per_order"))
    columns = ", ".join(f"{column} = ?" for column in changes)
    with conn:
        conn.execute(f"UPDATE products SET {columns} WHERE id = ?", (*changes.values(), product_id))
```

- 검사를 **먼저 전부** 하고 나서 UPDATE 한 번 → 값 하나라도 틀리면 아무것도 안 바뀐다 (부분 반영 없음).
- 컬럼 이름은 허용 목록(`name/price/stock/max_per_order/is_active`)으로 거른 뒤에만 SQL 에 들어간다 (값은 `?` 바인딩).
- 라우터는 `model_dump(exclude_unset=True)` 로 **보낸 항목만** 넘긴다. `null` 이 의미 있는 건 구매 제한 해제뿐이라 나머지 `null` 은 "안 바꿈" 으로 처리.

## 검증 포인트

| 테스트 | 확인 |
|---|---|
| `tests/api/test_admin_products.py::test_register_boundaries` (16케이스) | 가격 0✗/1✓/1천만✓/1천만+1✗, 재고 -1✗/0✓/99,999✓/100,000✗, 구매 제한 0✗/1✓/99✓/100✗, 이름 ""✗/공백만✗/50자✓/51자✗ |
| `test_update_invalid_value_changes_nothing` | `{"price": 9999, "stock": -1}` → 거부되고 **가격도 안 바뀜** |
| `test_price_change_does_not_affect_existing_order` | 볼펜 1,000원 주문 후 5,000원으로 올리면: 기존 주문 1,000원 그대로, 새 주문 5,000원 |
| `test_update_null_limit_removes_purchase_limit` | 한정판 구매 제한 해제 → 2개 주문 성공 |
| `test_deactivate_blocks_order_and_reactivate_allows` | 판매중지 → `PRODUCT_INACTIVE`, 재개 → 주문 성공 |
| `tests/e2e/test_shop_admin.py::test_admin_registers_product_and_shop_shows_it` | 어드민 등록 → 쇼핑몰에 12,000원 · "2개 남음" 표시 |
| `test_admin_stock_and_deactivate_reflect_in_shop` | 어드민 재고 0 → 쇼핑몰 "품절", 판매중지 → 쇼핑몰 버튼 "판매중지" 비활성 |

전체 213건 통과.

### 같이 고친 것
- 규칙 커버리지 리포트가 규칙 번호를 `R1~R15` 정규식으로 하드코딩하고 있어서 R16 을 "SPEC 에 없는 규칙" 으로 경고했다. **SPEC.md 의 규칙 표에서 규칙 목록을 읽게** 바꿔서, 앞으로 규칙을 추가해도 테스트 코드는 안 고쳐도 된다.
- E2E 가 보던 재고 표시(`admin-stock-*` 텍스트)가 입력칸으로 바뀌어서 `to_have_value` 로 바꿨다.

## 면접 예상 질문 & 답

**Q. 상품 가격을 바꾸면 이미 주문한 고객 금액은요?**
A. 주문 상품 테이블에 주문 당시 가격(`unit_price`)을 따로 저장하기 때문에 그대로입니다. 가격 변경 전 주문 금액이 유지되고 새 주문만 새 가격이 적용되는 걸 테스트로 고정했습니다.

**Q. 여러 항목을 한 번에 수정하다 하나가 잘못되면?**
A. 모든 값을 먼저 검사하고 통과해야 UPDATE 를 한 번 실행합니다. 그래서 "가격은 바뀌고 재고는 거부" 같은 부분 반영이 생기지 않습니다. 잘못된 값이 섞인 요청 후 상품 정보가 요청 전과 완전히 같은지 비교하는 테스트가 있습니다.

**Q. 경계값은 어떻게 정했나요?**
A. 각 입력의 최소 · 최대와 그 바로 바깥(0/1, 99,999/100,000 등)을 parametrize 한 표 하나로 16케이스를 검증합니다. 상품명은 빈 문자열과 공백만 있는 경우도 따로 넣었습니다.
