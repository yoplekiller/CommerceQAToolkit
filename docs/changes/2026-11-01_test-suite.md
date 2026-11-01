# 2026-11-01 테스트 스위트 고도화 (API R1~R15 + 웹 화면 E2E + CI)

## 무엇을 / 왜

| 추가 | 내용 |
|---|---|
| `tests/api/` 96건 | 서비스 API 를 SPEC 도메인 규칙 R1~R15 기준으로 전부 검증. 경계값·에러 코드·HTTP 상태코드까지 확인 |
| `tests/e2e/` 7건 | QA 도구 웹 화면을 실제 브라우저(Playwright)로 검증 |
| 규칙 커버리지 리포트 | `@pytest.mark.rule("R7")` 마커를 모아 실행 끝에 규칙별 테스트 수 출력, 테스트 없는 규칙 경고. `pytest --rule R7` 로 규칙별 실행 |
| QA 도구 `POST /qa/products` | 테스트 상품 생성. 시드 가격(전부 10원 단위)으로는 19,799원을 만들 수 없어서 경계값 테스트용으로 추가 |
| CI 2잡 분리 | API 잡 / E2E 잡. JUnit XML, 규칙 커버리지 요약(Actions Summary), E2E 실패 시 trace·스크린샷 artifact |

**왜:** 기존 테스트 23건은 "QA 도구가 맞는 데이터를 만드는지"만 봤고, 정작 테스트 대상 서비스의 규칙(재고, 배송비, 쿠폰, 상태 전이)은 검증하지 않았다. 이 레포의 이야기("QA 도구로 사전조건을 만들고 → 서비스를 검증한다")를 실제 테스트로 보여주는 게 목적.

## 발견한 버그 (테스트가 먼저 잡고 서비스 코드를 고침)

### 1. 장바구니에 0개/음수를 담으면 통과하고, 음수는 수량을 줄였다 (R15)
- **재현:** 볼펜 3개를 담은 뒤 `POST /users/{id}/cart {"product_id": 8, "quantity": -1}` → 200, 장바구니 수량 2
- **원인:** `add_to_cart` 가 "기존 수량 + 추가 수량" 합계만 1~99 로 검사했다. 3 + (-1) = 2 라 통과.
- **수정:** 추가 수량 자체를 먼저 1~99 로 검사 (`app/services.py` `add_to_cart`).
- **잡은 테스트:** `test_cart.py::test_add_invalid_quantity_rejected_even_if_already_in_cart[0/-1]`

### 2. 판매자 상태변경 API 로 구매확정·반품요청을 할 수 있었다 (R12)
- **재현:** 배송완료 주문에 `POST /orders/{id}/status {"status": "CONFIRMED", "actor": "seller"}` → 200, 이력에 판매자가 구매확정한 걸로 남음
- **원인:** 상태변경 API 가 전이표만 보고, 그 상태를 누가 만들 수 있는지는 안 봤다. SPEC 상 CONFIRMED / RETURN_REQUESTED 는 고객만 들어오는 상태.
- **수정:** `change_status_by_staff` 추가 — 고객 전용 상태(`CUSTOMER_ONLY_STATUSES`)면 403 `STATUS_NOT_ALLOWED_FOR_ACTOR`. 라우터가 이 함수를 쓰도록 변경. QA 도구 자동완료(`advance_to`)는 기존 `change_status` 를 그대로 써서 영향 없음.
- **잡은 테스트:** `test_order_status.py::test_staff_cannot_set_customer_only_status`

## 핵심 코드 (발췌)

**사전조건은 QA 도구, 본문은 검증만** — `tests/api/conftest.py` 의 `Shop` 래퍼
```python
def test_coupon_min_order_amount_boundary(shop, user, price, ok):
    product = shop.new_product(price=price)                # QA 도구: 19,799 / 19,800원 상품
    coupon = shop.coupon(user, min_order_amount=19800)     # QA 도구: 쿠폰 발급
    res = shop.order(user, {product: 1}, coupon)           # 검증 대상: 주문 API
    ...
```

**특정 상태 주문을 바로 만들기** — 상태 전이 테스트
```python
@pytest.mark.parametrize("from_status, to_status", NOT_ALLOWED)
def test_not_allowed_transition_rejected(shop, client, from_status, to_status):
    order = shop.order_in(from_status)        # QA 도구 주문 자동생성(target_status)
    assert_error(change(client, order["id"], to_status), 409, "INVALID_STATUS_TRANSITION")
    after = client.get(f"/orders/{order['id']}").json()
    assert after["status"] == from_status                       # 상태 그대로
    assert len(after["history"]) == len(order["history"])       # 이력도 안 늘어남
```

**E2E 서버 픽스처** — `tests/e2e/conftest.py`
```python
@pytest.fixture(scope="session")
def server(tmp_path_factory):
    port = _free_port()                       # 빈 포트
    env = {..., "DB_PATH": str(tmp_path_factory.mktemp("e2e") / "e2e.db")}   # 임시 DB
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", ...])
    ...  # /products 가 200 줄 때까지 대기 (최대 20초)

@pytest.fixture(scope="session")
def base_url(server):                         # page.goto("/") 가 테스트 서버로 가게
    return server

@pytest.fixture(autouse=True)
def fresh_data(qa):                           # 테스트마다 seed 로 초기화
    qa("POST", "/seed")
```

## 검증 포인트

| 규칙 | 대표 assert | 확인하는 것 |
|---|---|---|
| R1~R5 | `assert_error(res, 400, "PURCHASE_LIMIT_EXCEEDED")` | 상태코드 + 에러 코드를 **같이** 검증. 400 인데 코드가 다르면 다른 규칙이 막은 것이라 실패로 본다 |
| R1 | 판매중지 + 품절 상품 → `PRODUCT_INACTIVE` | 검사 **순서** (판매중지가 품절보다 먼저) |
| R5 | `[(SNEAKERS, 1), (SNEAKERS, 1)]` → 제한 초과 | 같은 상품 여러 줄을 **합친 수량**으로 검사 |
| R6 | 실패한 주문 뒤 `shop.stock(PEN) == before` | 여러 상품 중 하나라도 실패하면 **아무 재고도 안 줄어듦** (트랜잭션) |
| R7 | `(19799, 3000), (19800, 0), (19801, 0)` | 무료배송 경계값 3점 |
| R7·R10 | 19,800원 + 3,000원 쿠폰 → 배송비 0, 결제 16,800 | 무료배송 기준이 **할인 전** 금액 |
| R9 | `(8899, 8899), (8900, 8900), (8901, 8900)` | 정률 쿠폰 최대할인 경계 (89,000원의 10% = 8,900) |
| R9 | 5,980원의 3% → 179 | 원 단위 **버림** |
| R11 | 취소 후 같은 쿠폰으로 재주문 성공 | 쿠폰 복구를 "필드 값"이 아니라 **다시 쓸 수 있는지**로 확인 |
| R12 | 허용 8개 전부 + 비허용 8개 parametrize | 전이표 전체. 실패 시 상태·이력이 안 바뀌는 것까지 |
| R13·R14 | 취소 가능 2상태 / 불가 5상태, 반품 불가 5상태 | 고객 API 의 상태별 허용 범위 |
| E2E | `expect(options).to_have_text(["배송완료", "구매확정", "반품요청", "반품완료"])` | 배송중 주문 이동 목록에 "주문취소"가 **없다** (서버 전이표 기준) |
| E2E | 초기화 1번 클릭 후 `order-row-1` 여전히 보임 → 2번째 후 사라짐 | 실수 방지 2단계 동작 |

## 선택 이유

- **로케이터: `data-testid` 만 사용.** 화면 문구("주문 생성")나 CSS 클래스는 디자인 바꾸면 깨진다. 테스트 전용 속성이라 UI 를 고쳐도 테스트가 안정적이다. 단, 상태 배지 "배송완료" 같은 **사용자에게 보이는 결과**는 텍스트로 검증한다 (그게 검증 대상이라서).
- **대기: `time.sleep` 없음.** Playwright `expect(...).to_have_text()` 가 조건이 맞을 때까지 자동으로 재시도한다(기본 5초). 고정 sleep 은 느리거나(너무 길게) flaky 하다(너무 짧게).
- **사전조건: UI 로 만들지 않고 QA 도구 API 로.** "배송중 주문의 이동 목록" 을 테스트하는데 주문 생성 → 상태 이동까지 화면으로 클릭하면 느리고, 앞 단계가 깨지면 엉뚱한 테스트가 실패한다. 검증 대상이 아닌 준비 단계는 API 로 빠르게.
- **E2E 서버는 실제 uvicorn 프로세스.** TestClient 는 브라우저가 접속할 수 없어서 진짜 서버가 필요하다. 세션당 1번만 띄우고, 테스트마다 `seed` 로 데이터만 초기화해서 빠르다(7건 약 7초).
- **경계값 상품은 QA 도구로 생성.** 시드 상품 가격으로 19,799원을 못 만든다고 테스트를 포기하거나 서비스 코드를 건드리지 않고, 테스트 데이터를 만드는 도구를 늘렸다 — 이 레포가 말하려는 바로 그 방식.
- **규칙 마커.** "R1~R15 다 테스트했나?"를 사람이 세지 않게, 실행할 때마다 자동으로 표가 나온다. 테스트 없는 규칙은 경고.
- **CI 잡 분리.** 브라우저 설치가 필요 없는 API 테스트는 빠르게 먼저 결과를 주고, E2E 가 깨져도 API 결과는 따로 보인다.

## 면접 예상 질문 & 답

**Q. 테스트 사전조건은 어떻게 준비했나요?**
A. QA 도구 API(`/qa/*`)로 준비했습니다. 예를 들어 "배송완료 주문은 반품 요청이 된다"를 테스트하려면 주문을 배송완료까지 만들어야 하는데, 이걸 QA 도구 주문 자동생성에 `target_status=DELIVERED` 한 줄로 만듭니다. 이 도구는 DB 를 직접 고치지 않고 실제 서비스 로직을 거쳐서 만들기 때문에 "현실에선 생길 수 없는 데이터" 로 테스트하는 위험도 없습니다.

**Q. 경계값은 어떻게 정했나요?**
A. SPEC 규칙마다 기준값과 그 ±1 을 봤습니다. 무료배송은 19,799 / 19,800 / 19,801원, 수량은 0 / 1 / 99 / 100, 정률 쿠폰 최대할인은 계산값 8,900원 기준 8,899 / 8,900 / 8,901원입니다. 시드 데이터로 19,799원을 만들 수 없어서 QA 도구에 테스트 상품 생성 기능을 추가했습니다.

**Q. 테스트로 실제 버그를 찾은 적이 있나요?**
A. 두 개 찾았습니다. 하나는 장바구니에 이미 담긴 상품에 -1 개를 담으면 합계(3-1=2)만 검사해서 통과하고 수량이 줄어드는 문제였고, 다른 하나는 판매자 상태변경 API 로 고객 전용인 구매확정을 할 수 있던 문제였습니다. 테스트를 버그에 맞추지 않고 서비스 코드를 고쳤고, 그 테스트들이 회귀 테스트로 남아 있습니다.

**Q. E2E 테스트가 flaky 하지 않게 어떻게 했나요?**
A. 세 가지입니다. ① 로케이터는 `data-testid` 만 써서 문구·스타일 변경에 안 깨지게, ② `sleep` 없이 Playwright 의 자동 대기 assertion(`expect`)만 사용, ③ 테스트마다 데이터를 초기화하고 사전조건은 API 로 만들어 테스트 간 의존을 없앴습니다. 로컬에서 전체 스위트를 3번 연속 돌려 모두 통과하는 걸 확인했습니다.

**Q. E2E 가 CI 에서 실패하면 원인은 어떻게 보나요?**
A. 실패한 테스트만 Playwright trace 와 스크린샷을 artifact 로 올립니다. trace 를 `playwright show-trace` 로 열면 클릭 시점마다 화면, 네트워크 요청, 콘솔 로그를 볼 수 있어서 서버 에러인지 화면 문제인지 바로 구분됩니다.

**Q. 모든 규칙을 테스트했는지 어떻게 확인하나요?**
A. 테스트에 `@pytest.mark.rule("R7")` 처럼 SPEC 규칙 번호를 붙이고, conftest 훅이 실행 끝에 규칙별 테스트 수를 표로 출력합니다. 테스트가 없는 규칙은 경고가 뜨고, CI 에서는 이 표가 Actions 요약 화면에 나옵니다.
