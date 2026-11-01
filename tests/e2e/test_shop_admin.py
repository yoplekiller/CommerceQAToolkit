"""쇼핑몰(/shop) · 판매자 어드민(/admin) E2E.

고객 화면과 판매자 화면을 오가며 하나의 주문이 실제 흐름대로 진행되는지 본다.
사전조건(로그인된 회원, 배송완료 주문 등)은 QA 도구 API 로 만들어 화면 조작을 테스트 대상 부분으로만 줄인다.
"""
import pytest
import requests
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

WATER, SNEAKERS, TUMBLER, TISSUE, PEN = 2, 4, 5, 7, 8


@pytest.fixture
def buyer(server):
    """쇼핑몰 회원 1명 (QA 도구로 생성)."""
    res = requests.post(f"{server}/qa/users/bulk", json={"count": 1, "prefix": "buyer"},
                        headers={"X-QA-Token": "local-qa-token"}, timeout=10)
    return res.json()[0]


@pytest.fixture
def shop(page, buyer):
    """buyer 로 로그인한 쇼핑몰 화면."""
    page.goto("/shop")
    page.get_by_test_id("login-email").fill(buyer["email"])
    page.get_by_test_id("login-button").click()
    expect(page.get_by_test_id("login-user")).to_contain_text(buyer["email"])
    return page


@pytest.fixture
def admin(context):
    """같은 브라우저의 새 탭에 연 판매자 어드민 (고객 화면과 동시에 사용)."""
    page = context.new_page()
    page.goto("/admin")
    page.get_by_test_id("admin-product-1").wait_for()
    return page


def badge(page, testid):
    return page.get_by_test_id(testid).get_by_test_id("status-badge")


# ---------- 쇼핑몰 ----------

def test_guest_cannot_add_to_cart(page):
    """비로그인 상태로 장바구니에 담으면 LOGIN_REQUIRED 안내가 뜬다."""
    page.goto("/shop")
    page.get_by_test_id(f"add-cart-{WATER}").click()
    expect(page.get_by_test_id("toast-error")).to_contain_text("LOGIN_REQUIRED")


def test_unavailable_products_cannot_be_ordered(page):
    """품절·판매중지 상품은 담기/바로구매 버튼이 비활성화되고 사유가 표시된다."""
    page.goto("/shop")
    expect(page.get_by_test_id(f"add-cart-{TUMBLER}")).to_be_disabled()
    expect(page.get_by_test_id(f"add-cart-{TUMBLER}")).to_have_text("품절")
    expect(page.get_by_test_id("add-cart-6")).to_have_text("판매중지")
    expect(page.get_by_test_id(f"buy-now-{TUMBLER}")).to_be_disabled()


def test_cart_shows_free_shipping_boundary(shop):
    """장바구니 금액은 서버 주문서 계산 결과: 19,800원 미만이면 배송비와 남은 금액 안내, 이상이면 무료배송 (R7)."""
    shop.get_by_test_id(f"qty-{WATER}").fill("2")
    shop.get_by_test_id(f"add-cart-{WATER}").click()
    expect(shop.get_by_test_id("quote-shipping")).to_have_text("3,000원")
    expect(shop.get_by_test_id("free-shipping-hint")).to_have_text("7,840원 더 담으면 무료배송")  # 19,800 - 11,960

    shop.get_by_test_id(f"add-cart-{TISSUE}").click()
    expect(shop.get_by_test_id("quote-shipping")).to_have_text("무료")
    expect(shop.get_by_test_id("quote-total")).to_have_text("31,760원")


def test_purchase_limit_error_shown_in_checkout(shop):
    """한정판(1회 1개) 2개 바로구매 시 주문서에 PURCHASE_LIMIT_EXCEEDED 가 표시되고 결제 버튼이 막힌다 (R4)."""
    shop.get_by_test_id(f"qty-{SNEAKERS}").fill("2")
    shop.get_by_test_id(f"buy-now-{SNEAKERS}").click()
    expect(shop.get_by_test_id("quote-error")).to_contain_text("PURCHASE_LIMIT_EXCEEDED")
    expect(shop.get_by_test_id("pay-button")).to_be_disabled()


def test_coupon_applied_in_checkout(shop, qa, buyer):
    """QA 도구로 발급한 3,000원 쿠폰을 고르면 할인과 결제금액이 바뀐다 (R9)."""
    qa("POST", "/coupons/issue", {"user_id": buyer["id"], "discount_type": "FIXED", "value": 3000})
    shop.reload()
    shop.get_by_test_id(f"add-cart-{TISSUE}").click()
    expect(shop.get_by_test_id("quote-total")).to_have_text("19,800원")

    shop.get_by_test_id("coupon-select").select_option(index=1)
    expect(shop.get_by_test_id("quote-discount")).to_have_text("-3,000원")
    expect(shop.get_by_test_id("quote-total")).to_have_text("16,800원")


def test_checkout_creates_order_and_empties_cart(shop):
    """결제하면 내 주문내역에 결제완료 주문이 생기고 장바구니가 비워진다 (R15)."""
    shop.get_by_test_id(f"add-cart-{PEN}").click()
    shop.get_by_test_id("pay-button").click()
    expect(badge(shop, "my-order-1")).to_have_text("결제완료")
    expect(shop.get_by_test_id("cart-empty")).to_be_visible()


def test_customer_can_cancel_only_before_shipping(shop, qa, buyer):
    """결제완료 주문에는 취소 버튼이 있고, 배송중 주문에는 없다 (R13)."""
    qa("POST", "/orders/generate", {"count": 1, "user_id": buyer["id"], "target_status": "PAID"})
    qa("POST", "/orders/generate", {"count": 1, "user_id": buyer["id"], "target_status": "SHIPPING"})
    shop.get_by_test_id("orders-refresh").click()

    expect(shop.get_by_test_id("cancel-2")).to_have_count(0)
    shop.get_by_test_id("cancel-1").click()
    expect(badge(shop, "my-order-1")).to_have_text("주문취소")


# ---------- 판매자 어드민 ----------

def test_admin_status_tabs_count_and_filter(admin, qa):
    """상태 탭 숫자가 상태별 주문 수와 같고, 탭을 누르면 그 상태 주문만 보인다."""
    qa("POST", "/orders/generate", {"count": 2, "target_status": "PAID"})
    qa("POST", "/orders/generate", {"count": 1, "target_status": "SHIPPING"})
    admin.get_by_test_id("admin-refresh").click()

    expect(admin.get_by_test_id("tab-count-ALL")).to_have_text("3")
    expect(admin.get_by_test_id("tab-count-PAID")).to_have_text("2")
    admin.get_by_test_id("tab-SHIPPING").click()
    # 표 자체의 testid(admin-order-table)도 같은 접두사라 tbody 행으로 범위를 좁힌다
    rows = admin.get_by_test_id("admin-order-table").locator('tbody tr[data-testid^="admin-order-"]')
    expect(rows).to_have_count(1)
    expect(badge(admin, "admin-order-3")).to_have_text("배송중")


def test_admin_bulk_prepare_reports_partial_failure(admin, qa):
    """일괄 상품준비: 결제완료 2건은 성공, 이미 배송중인 1건은 서버가 거부해 실패로 집계된다."""
    qa("POST", "/orders/generate", {"count": 2, "target_status": "PAID"})
    qa("POST", "/orders/generate", {"count": 1, "target_status": "SHIPPING"})
    admin.get_by_test_id("admin-refresh").click()

    admin.get_by_test_id("check-all").check()
    admin.get_by_test_id("bulk-prepare").click()
    expect(admin.get_by_test_id("toast").last).to_contain_text("성공 2건 · 실패 1건 (#3)")
    expect(badge(admin, "admin-order-1")).to_have_text("상품준비중")
    expect(badge(admin, "admin-order-3")).to_have_text("배송중")


# ---------- 고객 ↔ 판매자 전체 흐름 ----------

def test_full_order_return_flow_across_shop_and_admin(shop, admin):
    """고객 주문 → 판매자 상품준비/발송 → 택배사 배송완료 → 고객 반품 신청 → 판매자 반품 승인 → 재고 복구 (R11)."""
    stock_before = admin.get_by_test_id(f"admin-stock-{TISSUE}").inner_text()
    shop.get_by_test_id(f"add-cart-{TISSUE}").click()
    shop.get_by_test_id("pay-button").click()
    expect(badge(shop, "my-order-1")).to_have_text("결제완료")

    admin.get_by_test_id("admin-refresh").click()
    expect(admin.get_by_test_id(f"admin-stock-{TISSUE}")).to_have_text(str(int(stock_before) - 1))
    for to, label in (("PREPARING", "상품준비중"), ("SHIPPING", "배송중"), ("DELIVERED", "배송완료")):
        admin.get_by_test_id(f"action-1-{to}").click()
        expect(badge(admin, "admin-order-1")).to_have_text(label)

    shop.get_by_test_id("orders-refresh").click()
    expect(badge(shop, "my-order-1")).to_have_text("배송완료")
    shop.get_by_test_id("return-1").click()
    expect(badge(shop, "my-order-1")).to_have_text("반품요청")

    admin.get_by_test_id("admin-refresh").click()
    expect(admin.get_by_test_id("tab-count-RETURN_REQUESTED")).to_have_text("1")
    admin.get_by_test_id("action-1-RETURNED").click()
    expect(badge(admin, "admin-order-1")).to_have_text("반품완료")
    expect(admin.get_by_test_id(f"admin-stock-{TISSUE}")).to_have_text(stock_before)

    shop.get_by_test_id("orders-refresh").click()
    shop.get_by_test_id("timeline-1").click()
    timeline = shop.get_by_test_id("timeline-body-1")
    for actor_line in ("결제완료 (customer)", "배송완료 (carrier)", "반품요청 (customer)", "반품완료 (seller)"):
        expect(timeline).to_contain_text(actor_line)
