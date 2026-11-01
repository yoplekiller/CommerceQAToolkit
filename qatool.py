"""QA 도구 CLI. 서버(uvicorn)가 떠 있는 상태에서 사용한다.

    python qatool.py seed
    python qatool.py orders gen --count 5 --scenario coupon_fixed --status DELIVERED
    python qatool.py orders advance 3 --to RETURNED
"""
import json
import os
import sys
import unicodedata

import click
import requests

BASE_URL = os.getenv("QA_BASE_URL", "http://127.0.0.1:8000")
QA_TOKEN = os.getenv("QA_TOKEN", "local-qa-token")
STATUSES = ["PAID", "PREPARING", "SHIPPING", "DELIVERED", "CONFIRMED", "CANCELED", "RETURN_REQUESTED", "RETURNED"]
SCENARIOS = ["basic", "multi_item", "coupon_fixed", "coupon_percent", "free_shipping", "paid_shipping"]


def call(method: str, path: str, body: dict | None = None):
    try:
        res = requests.request(method, BASE_URL + path, json=body, headers={"X-QA-Token": QA_TOKEN}, timeout=30)
    except requests.ConnectionError:
        click.secho(f"서버에 연결할 수 없습니다: {BASE_URL} (uvicorn 실행 여부 확인)", fg="red")
        sys.exit(1)
    data = res.json()
    if res.status_code >= 400:
        error = data.get("error") or {"code": res.status_code, "message": data.get("detail")}
        click.secho(f"[{error['code']}] {error['message']}", fg="red")
        sys.exit(1)
    return data


def rjust(text: str, width: int) -> str:
    """한글은 터미널에서 2칸을 차지해서 str.rjust 로는 열이 어긋난다."""
    display = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return " " * max(width - display, 0) + text


def print_orders(orders: list[dict]) -> None:
    headers = [("ID", 4), ("상태", 18), ("상품합계", 10), ("배송비", 8), ("할인", 8), ("결제금액", 10)]
    click.echo("".join(rjust(h, w) for h, w in headers) + "  상품")
    for o in orders:
        products = ", ".join(f"{i['product_name']}({i['quantity']}개)" for i in o["items"])
        click.echo(
            f"{o['id']:>4}{o['status']:>18}{o['items_amount']:>10,}"
            f"{o['shipping_fee']:>8,}{o['discount_amount']:>8,}{o['total_amount']:>10,}  {products}"
        )


def print_products(products: list[dict]) -> None:
    for p in products:
        tags = []
        if not p["is_active"]:
            tags.append("판매중지")
        if p["stock"] == 0:
            tags.append("품절")
        if p["max_per_order"] is not None:
            tags.append(f"1회 {p['max_per_order']}개 제한")
        suffix = f" [{', '.join(tags)}]" if tags else ""
        click.echo(f"#{p['id']} {p['name']} {p['price']:,}원 (재고 {p['stock']}){suffix}")


@click.group()
def cli():
    """커머스 QA 도구"""


@cli.command()
def seed():
    """전체 초기화 후 기본 상품 생성"""
    print_products(call("POST", "/qa/seed")["products"])


@cli.command()
@click.confirmation_option(prompt="모든 데이터를 지웁니다. 계속할까요?")
def reset():
    """전체 데이터 초기화"""
    call("POST", "/qa/reset")
    click.echo("초기화 완료")


@cli.command()
@click.option("--count", default=1, show_default=True)
@click.option("--prefix", default="qa", show_default=True)
def users(count, prefix):
    """테스트 계정 일괄 생성"""
    for u in call("POST", "/qa/users/bulk", {"count": count, "prefix": prefix}):
        click.echo(f"#{u['id']} {u['email']}")


@cli.command()
@click.option("--user", "user_id", type=int, required=True)
@click.option("--type", "discount_type", type=click.Choice(["FIXED", "PERCENT"]), required=True)
@click.option("--value", type=int, required=True, help="FIXED=원, PERCENT=%")
@click.option("--min", "min_order_amount", type=int, default=0, show_default=True)
@click.option("--max", "max_discount", type=int, default=None, help="정률 쿠폰 최대 할인금액")
@click.option("--expires-days", type=int, default=30, show_default=True, help="음수면 이미 만료된 쿠폰")
def coupon(user_id, discount_type, value, min_order_amount, max_discount, expires_days):
    """쿠폰 발급"""
    c = call("POST", "/qa/coupons/issue", {
        "user_id": user_id, "discount_type": discount_type, "value": value,
        "min_order_amount": min_order_amount, "max_discount": max_discount, "expires_in_days": expires_days,
    })
    click.echo(f"#{c['id']} {c['code']} {c['discount_type']} {c['value']} (만료 {c['expires_at']})")


@cli.command()
@click.argument("product_id", type=int)
@click.argument("stock", type=int)
def stock(product_id, stock):
    """상품 재고 설정 (0 이면 품절)"""
    print_products([call("PUT", f"/qa/products/{product_id}/stock", {"stock": stock})])


@cli.command()
@click.option("--user", "user_id", type=int, required=True)
@click.option("--count", default=3, show_default=True, help="담을 상품 종류 수")
@click.option("--seed", "seed_value", type=int, default=None)
def cart(user_id, count, seed_value):
    """장바구니 자동 담기"""
    for c in call("POST", "/qa/carts/fill", {"user_id": user_id, "count": count, "seed": seed_value}):
        click.echo(f"{c['product_name']} {c['unit_price']:,}원 ({c['quantity']}개)")


@cli.group()
def orders():
    """주문 도구"""


@orders.command("gen")
@click.option("--count", default=1, show_default=True)
@click.option("--scenario", type=click.Choice(SCENARIOS), default="basic", show_default=True)
@click.option("--status", "target_status", type=click.Choice(STATUSES), default="PAID", show_default=True)
@click.option("--user", "user_id", type=int, default=None, help="생략하면 테스트 계정을 새로 만든다")
@click.option("--seed", "seed_value", type=int, default=None, help="같은 값이면 같은 주문 데이터 재현")
@click.option("--json", "as_json", is_flag=True, help="JSON 으로 출력")
def orders_gen(count, scenario, target_status, user_id, seed_value, as_json):
    """주문 자동생성 (+ 목표 상태까지 자동 진행)"""
    data = call("POST", "/qa/orders/generate", {
        "count": count, "scenario": scenario, "target_status": target_status,
        "user_id": user_id, "seed": seed_value,
    })
    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_orders(data)


@orders.command("advance")
@click.argument("order_id", type=int)
@click.option("--to", "target_status", type=click.Choice(STATUSES), required=True)
def orders_advance(order_id, target_status):
    """주문 상태 자동완료 (허용된 전이를 한 단계씩 진행)"""
    order = call("POST", f"/qa/orders/{order_id}/advance", {"target_status": target_status})
    print_orders([order])
    click.echo("이력: " + " -> ".join(h["to_status"] for h in order["history"]))


@orders.command("show")
@click.argument("order_id", type=int)
def orders_show(order_id):
    """주문 상세"""
    order = call("GET", f"/orders/{order_id}")
    print_orders([order])
    for h in order["history"]:
        click.echo(f"  {h['changed_at']}  {h['from_status'] or '-':<16} -> {h['to_status']:<16} ({h['actor']})")


if __name__ == "__main__":
    cli()
