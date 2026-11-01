"""대상 서비스와 상관없는 QA 도구 로직: 주문 자동생성 · 상태 자동완료.

어댑터(adapter.py)가 표준 형식으로 바꿔주는 값만 쓰므로, 설정 파일만 바꾸면 어떤 서비스에도 같은 로직이 돈다.
이 레포의 내장 QA 도구(app/qa_tools.py)도 상품 고르기(pick_items)는 이 함수를 같이 쓴다.
"""
import random
import secrets
from collections import deque

SCENARIOS = ("basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping")


class ScenarioError(Exception):
    """이 서비스/데이터로는 요청한 시나리오를 만들 수 없음 (상품 부족, 엔드포인트 미설정 등)."""


def max_quantity(product: dict, limit: int = 99) -> int:
    """한 주문에 담을 수 있는 최대 수량 (재고, 수량 상한, 1회 구매 제한 중 가장 작은 값)."""
    limits = [product["stock"], limit]
    if product.get("max_per_order") is not None:
        limits.append(product["max_per_order"])
    return min(limits)


def pick_items(rng: random.Random, products: list[dict], scenario: str,
               free_shipping_threshold: int, quantity_limit: int = 99) -> list[dict]:
    """시나리오에 맞게 주문할 상품과 수량을 고른다. products 는 주문 가능한 상품만 (id, price, stock, max_per_order)."""
    if scenario == "paid_shipping":
        cheap = [p for p in products if p["price"] < free_shipping_threshold]
        if not cheap:
            raise ScenarioError("무료배송 기준보다 싼 주문 가능 상품이 없습니다.")
        return [{"product_id": rng.choice(cheap)["id"], "quantity": 1}]

    kinds = 2 if scenario == "multi_item" else 1
    if len(products) < kinds:
        raise ScenarioError(f"주문 가능한 상품이 {kinds}종 미만입니다.")
    if scenario == "free_shipping":
        # 재고 · 구매 제한 안에서 혼자 무료배송 기준을 채울 수 있는 상품을 우선 고른다
        # (재고가 줄어든 싼 상품을 골라 기준을 못 채우고 실패하는 걸 막기 위해)
        capable = [p for p in products if p["price"] * max_quantity(p, quantity_limit) >= free_shipping_threshold]
        products = capable or products
    chosen = rng.sample(products, k=kinds)
    quantities = {p["id"]: 1 for p in chosen}

    if scenario == "free_shipping":
        # 무료배송 기준을 넘을 때까지 수량을 늘린다 (재고/구매제한 안에서)
        by_id = {p["id"]: p for p in chosen}
        while sum(by_id[pid]["price"] * q for pid, q in quantities.items()) < free_shipping_threshold:
            growable = [pid for pid, q in quantities.items() if q < max_quantity(by_id[pid], quantity_limit)]
            if not growable:
                raise ScenarioError("재고 안에서 무료배송 기준을 채울 수 없습니다.")
            quantities[rng.choice(growable)] += 1
    return [{"product_id": pid, "quantity": q} for pid, q in quantities.items()]


def find_path(transitions: dict, current: str, target: str) -> list[str] | None:
    """허용된 전이만으로 current → target 까지 가장 짧은 경로 (current 제외). 갈 수 없으면 None."""
    queue = deque([(current, [])])
    seen = {current}
    while queue:
        status, path = queue.popleft()
        if status == target:
            return path
        for nxt in transitions.get(status, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
    return None


def advance(adapter, order_id, target: str) -> dict:
    """주문을 목표 상태까지 정상 단계로 한 단계씩 진행한다 (대상 서비스의 상태 변경 API 를 단계마다 호출)."""
    order = adapter.get_order(order_id)
    path = find_path(adapter.config["transitions"], order["status"], target)
    if path is None:
        raise ScenarioError(f"주문 {order_id}: {order['status']} 에서 {target} 로 갈 수 없습니다 (transitions 확인).")
    for status in path:
        adapter.change_status(order_id, status)
    order = adapter.get_order(order_id)
    if order["status"] != target:
        raise ScenarioError(f"주문 {order_id}: 상태 변경 후 {order['status']} 입니다 ({target} 기대). change_status 설정 확인.")
    return order


def generate_orders(adapter, count: int, scenario: str = "basic", target_status: str = "PAID",
                    user_id=None, seed: int | None = None) -> list[dict]:
    """주문 자동생성: 대상 서비스 API 로 주문을 만들고 목표 상태까지 진행한다."""
    if scenario not in SCENARIOS:
        raise ScenarioError(f"지원하지 않는 시나리오: {scenario} (가능: {', '.join(SCENARIOS)})")
    rules = adapter.config["rules"]
    if scenario in ("free_shipping", "paid_shipping") and not rules["free_shipping_threshold"]:
        raise ScenarioError("rules.free_shipping_threshold 가 설정되지 않아 배송비 시나리오를 만들 수 없습니다.")
    if scenario.startswith("coupon") and not adapter.has("issue_coupon"):
        raise ScenarioError("endpoints.issue_coupon 이 설정되지 않아 쿠폰 시나리오를 만들 수 없습니다.")
    if user_id is None:
        if not adapter.has("create_user"):
            raise ScenarioError("회원 ID 를 지정하거나 endpoints.create_user 를 설정하세요.")
        tag = secrets.token_hex(4)
        user_id = adapter.create_user(f"qa+{tag}@test.local", f"qa-{tag}")

    rng = random.Random(seed)
    orders = []
    for _ in range(count):
        # 앞 주문에서 재고가 빠졌을 수 있어서 매번 다시 조회
        products = [p for p in adapter.list_products() if p["active"] and p["stock"] > 0]
        if not products:
            raise ScenarioError("주문 가능한 상품(판매중 + 재고 있음)이 없습니다.")
        items = pick_items(rng, products, scenario, rules["free_shipping_threshold"], rules["max_quantity"])

        coupon_id = None
        if scenario == "coupon_fixed":
            coupon_id = adapter.issue_coupon(user_id, "FIXED", 3000)
        elif scenario == "coupon_percent":
            coupon_id = adapter.issue_coupon(user_id, "PERCENT", 10, max_discount=5000)

        order = adapter.create_order(user_id, items, coupon_id)
        orders.append(advance(adapter, order["id"], target_status) if target_status != order["status"] else order)
    return orders
