"""설정 점검: 빈칸 · 필수 항목 · 연결 · 응답 필드 매핑을 확인한다. **읽기 요청(list_products)만** 보내고 데이터는 만들지 않는다."""
from qa_target.adapter import HttpAdapter, TargetError
from qa_target.config import OPTIONAL_ENDPOINTS, validate

SCENARIO_NEEDS = {
    "기본 / 여러상품 주문": [],
    "쿠폰 주문 (coupon_fixed / coupon_percent)": ["issue_coupon"],
    "회원 자동 생성 (회원 ID 없이 주문 생성)": ["create_user"],
    "재고 조정 (품절 만들기)": ["set_stock"],
}


def run_check(config: dict, client=None) -> list[tuple[bool, str]]:
    """(통과 여부, 설명) 목록. 설정 문제가 있으면 연결 점검은 하지 않는다."""
    results: list[tuple[bool, str]] = []
    problems = validate(config)
    if problems:
        return [(False, p) for p in problems]
    results.append((True, f"설정 형식 OK · 대상 {config.get('name', '')} ({config['environment']}) {config['base_url']}"))

    adapter = HttpAdapter(config, client=client)
    try:
        products = adapter.list_products()
    except TargetError as e:
        return results + [(False, f"상품 조회 실패: {e}")]
    results.append((True, f"연결 OK · 상품 {len(products)}개 조회, 필드 매핑(id/price/stock) 확인"))
    orderable = [p for p in products if p["active"] and p["stock"] > 0]
    results.append((bool(orderable), f"주문 가능한 상품 {len(orderable)}개" + ("" if orderable else " — 주문 생성 불가")))

    for feature, needs in SCENARIO_NEEDS.items():
        missing = [n for n in needs if not adapter.has(n)]
        results.append((not missing, f"{feature}: " + ("사용 가능" if not missing else f"endpoints.{', '.join(missing)} 미설정")))
    unset = [n for n in OPTIONAL_ENDPOINTS if not adapter.has(n)]
    if not config["rules"]["free_shipping_threshold"]:
        results.append((False, "배송비 시나리오: rules.free_shipping_threshold 미설정"))
    if unset:
        results.append((True, f"(선택 기능 미설정: {', '.join(unset)} — 없어도 기본 주문 생성 · 상태 진행은 동작)"))
    return results
