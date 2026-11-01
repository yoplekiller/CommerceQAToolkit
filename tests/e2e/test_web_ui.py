"""QA 도구 웹 화면 E2E (Playwright). 로케이터는 전부 data-testid, 대기는 expect 자동 대기."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_generate_orders_appear_in_table(app_page):
    """주문 자동생성: 무료배송 + 배송완료 2건을 만들면 표에 2행이 생기고 배송비가 0원이다."""
    page = app_page
    page.get_by_test_id("gen-scenario").select_option("free_shipping")
    page.get_by_test_id("gen-status").select_option("DELIVERED")
    page.get_by_test_id("gen-count").fill("2")
    page.get_by_test_id("gen-button").click()

    expect(page.get_by_test_id("gen-result")).to_contain_text("2건 생성")
    for order_id in (1, 2):
        row = page.get_by_test_id(f"order-row-{order_id}")
        expect(row.get_by_test_id("status-badge")).to_have_text("배송완료")
        expect(row.locator("td").nth(5)).to_have_text("0")  # 배송비 열


def test_move_status_updates_badge_and_history(app_page, qa):
    """상태 이동: 결제완료 주문을 배송중으로 옮기면 배지가 바뀌고, 이력에 중간 단계까지 남는다."""
    qa("POST", "/orders/generate", {"count": 1, "target_status": "PAID"})
    page = app_page
    page.get_by_test_id("order-refresh").click()

    page.get_by_test_id("move-select-1").select_option("SHIPPING")
    page.get_by_test_id("move-button-1").click()
    expect(page.get_by_test_id("order-row-1").get_by_test_id("status-badge")).to_have_text("배송중")

    page.get_by_test_id("order-row-1").get_by_text("#1").click()
    history = page.locator("tr.history")
    expect(history).to_contain_text("결제완료 → 상품준비중 (qa-tool)")
    expect(history).to_contain_text("상품준비중 → 배송중 (qa-tool)")


def test_terminal_status_has_no_move_menu(app_page, qa):
    """구매확정(종료 상태) 주문에는 이동 메뉴 대신 '종료'가 보인다."""
    qa("POST", "/orders/generate", {"count": 1, "target_status": "CONFIRMED"})
    page = app_page
    page.get_by_test_id("order-refresh").click()

    row = page.get_by_test_id("order-row-1")
    expect(row).to_contain_text("종료")
    expect(page.get_by_test_id("move-select-1")).to_have_count(0)


def test_shipping_order_cannot_be_moved_to_canceled(app_page, qa):
    """배송중 주문의 이동 목록에는 '주문취소'가 없다 (서버 전이표 기준으로 갈 수 있는 상태만)."""
    qa("POST", "/orders/generate", {"count": 1, "target_status": "SHIPPING"})
    page = app_page
    page.get_by_test_id("order-refresh").click()

    options = page.get_by_test_id("move-select-1").locator("option")
    expect(options).to_have_text(["배송완료", "구매확정", "반품요청", "반품완료"])


def test_sold_out_button_sets_stock_to_zero(app_page):
    """[품절] 버튼을 누르면 재고 칸이 0 이 되고 품절 태그가 붙는다."""
    page = app_page
    page.get_by_test_id("stock-soldout-1").click()

    expect(page.get_by_test_id("stock-input-1")).to_have_value("0")
    expect(page.get_by_test_id("product-row-1")).to_contain_text("품절")


def test_error_toast_shows_error_code(app_page):
    """서버가 거부하면 에러 토스트에 에러 코드가 보인다: 없는 회원에게 쿠폰 발급 -> USER_NOT_FOUND."""
    page = app_page
    page.get_by_test_id("coupon-user").fill("9999")
    page.get_by_test_id("coupon-button").click()

    expect(page.get_by_test_id("toast-error")).to_contain_text("USER_NOT_FOUND")
    expect(page.get_by_test_id("coupon-result")).to_have_text("")


def test_reset_requires_second_click(app_page, qa):
    """초기화는 한 번 누르면 확인 상태만 되고, 두 번째 클릭에서 주문이 지워진다."""
    qa("POST", "/orders/generate", {"count": 1})
    page = app_page
    page.get_by_test_id("order-refresh").click()
    expect(page.get_by_test_id("order-row-1")).to_be_visible()

    seed = page.get_by_test_id("seed-button")
    seed.click()
    expect(seed).to_have_text("한 번 더 누르면 초기화")
    expect(page.get_by_test_id("order-row-1")).to_be_visible()

    seed.click()
    expect(page.get_by_test_id("order-row-1")).to_have_count(0)
    expect(page.get_by_test_id("order-table")).to_contain_text("주문이 없습니다")
