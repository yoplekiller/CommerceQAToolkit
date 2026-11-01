"""외부 서비스 연결(qa_target): 설정 파일만으로 다른 서비스에 QA 도구를 붙일 수 있는지.

1) 이 레포의 데모 쇼핑몰을 targets/qa-mall.yaml 로 연결 (QA 도구 내부 함수 없이 공개 API 만)
2) 주소 · 응답 구조 · 상태값 · ID 형식이 전혀 다른 가짜 쇼핑몰을 설정만 바꿔서 연결
"""
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from qa_target import engine
from qa_target.adapter import HttpAdapter, TargetError, render
from qa_target.check import run_check
from qa_target.config import ConfigError, load_config, validate
from qa_target.engine import ScenarioError

TARGETS = Path(__file__).resolve().parents[1] / "targets"


# ---------- 설정 읽기 ----------

def test_template_reports_every_blank():
    """템플릿은 TODO 칸을 전부 '안 채운 칸'으로 알려주고, 연결은 시도하지 않는다."""
    config = load_config(TARGETS / "template.yaml")
    problems = validate(config)
    assert "아직 안 채운 칸: base_url" in problems
    assert "아직 안 채운 칸: statuses.PAID" in problems
    assert "환경변수가 없습니다: TODO_API_TOKEN" in problems
    assert all(not ok for ok, _ in run_check(config))


@pytest.mark.parametrize("environment", ["production", "PROD", "live", "운영"])
def test_production_is_refused(environment):
    """운영 환경 설정은 읽는 단계에서 거부한다."""
    with pytest.raises(ConfigError, match="운영 환경"):
        load_config({"environment": environment, "base_url": "https://api.example.com"})


def test_env_substitution(monkeypatch):
    """${VAR} 는 환경변수, ${VAR:-기본값} 은 없을 때 기본값, 없는 변수는 missing 으로 보고."""
    monkeypatch.setenv("SHOP_TOKEN", "abc")
    config = load_config({
        "environment": "staging", "base_url": "${SHOP_URL:-http://localhost:9000}",
        "auth": {"headers": {"Authorization": "Bearer ${SHOP_TOKEN}", "X-Extra": "${NOT_SET}"}},
    })
    assert config["base_url"] == "http://localhost:9000"
    assert config["auth"]["headers"]["Authorization"] == "Bearer abc"
    assert config["_missing_env"] == ["NOT_SET"]


def test_render_keeps_types_and_drops_none():
    """본문 템플릿: '{x}' 전체면 타입 유지, None 이면 키 제거, 문자열 일부면 끼워 넣기."""
    body = render({"user": "{user_id}", "items": "{items}", "coupon": "{coupon_id}", "memo": "QA-{user_id}"},
                  {"user_id": 7, "items": [{"id": 1}], "coupon_id": None})
    assert body == {"user": 7, "items": [{"id": 1}], "memo": "QA-7"}


# ---------- 1) 이 레포의 데모 쇼핑몰을 설정 파일로 연결 ----------

@pytest.fixture
def qa_mall(client):
    """targets/qa-mall.yaml 로 만든 어댑터 (요청은 테스트 앱으로)."""
    return HttpAdapter(load_config(TARGETS / "qa-mall.yaml"), client=client)


def test_qa_mall_config_check_passes(client):
    """데모 쇼핑몰 설정은 점검 항목이 전부 통과한다."""
    results = run_check(load_config(TARGETS / "qa-mall.yaml"), client=client)
    assert all(ok for ok, _ in results), results


def test_generate_orders_through_public_apis(client, qa_mall):
    """설정만으로 쿠폰 주문 3건 → 배송완료. 이력에 판매자/택배사 API 를 거친 흔적(seller/carrier)이 남는다."""
    orders = engine.generate_orders(qa_mall, 3, "coupon_percent", "DELIVERED", seed=1)
    assert [o["status"] for o in orders] == ["DELIVERED"] * 3
    for o in orders:
        detail = client.get(f"/orders/{o['id']}").json()
        assert detail["discount_amount"] > 0
        assert [h["actor"] for h in detail["history"]] == ["customer", "seller", "seller", "carrier"]


def test_advance_to_returned_restores_stock(client, qa_mall):
    """설정의 상태별 엔드포인트(반품요청=/return, 반품완료=/status)로 반품까지 가면 재고가 돌아온다."""
    before = {p["id"]: p["stock"] for p in client.get("/products").json()}
    order = engine.generate_orders(qa_mall, 1, "basic", "DELIVERED", seed=3)[0]
    order = engine.advance(qa_mall, order["id"], "RETURNED")
    assert order["status"] == "RETURNED"
    assert {p["id"]: p["stock"] for p in client.get("/products").json()} == before


def test_service_rule_errors_surface_as_target_error(qa_mall):
    """대상 서비스가 거부한 요청(한정판 2개)은 HTTP 상태와 응답 본문을 담은 TargetError 로 올라온다."""
    user = qa_mall.create_user("limit@test.local", "limit")
    with pytest.raises(TargetError, match="HTTP 400.*PURCHASE_LIMIT_EXCEEDED"):
        qa_mall.create_order(user, [{"product_id": 4, "quantity": 2}])


def test_wrong_field_mapping_is_reported(client):
    """응답에 없는 필드로 매핑하면 점검이 어느 필드인지 알려준다."""
    config = load_config(TARGETS / "qa-mall.yaml")
    config["fields"]["product"]["price"] = "sale_price"
    results = run_check(config, client=client)
    assert any("sale_price" in message for ok, message in results if not ok)


# ---------- 2) 모양이 전혀 다른 가짜 쇼핑몰 ----------

def other_shop_app() -> FastAPI:
    """다른 회사 API 흉내: /api/v1 경로, {"result": ...} 로 감싼 응답, 문자열 ID, 다른 상태값, 상태별 액션 URL, 반품 없음, Bearer 인증."""
    app = FastAPI()
    goods = {"G-1": {"goodsNo": "G-1", "goodsNm": "양말", "salePrice": {"amount": 3000}, "inventory": 10, "onSale": True},
             "G-2": {"goodsNo": "G-2", "goodsNm": "모자", "salePrice": {"amount": 25000}, "inventory": 2, "onSale": True},
             "G-3": {"goodsNo": "G-3", "goodsNm": "단종", "salePrice": {"amount": 9000}, "inventory": 5, "onSale": False}}
    orders: dict[str, dict] = {}
    flow = {"accept": ("ORDER_COMPLETE", "PREPARE"), "ship": ("PREPARE", "DELIVERY_ING"),
            "deliver": ("DELIVERY_ING", "DELIVERY_DONE"), "confirm": ("DELIVERY_DONE", "PURCHASE_CONFIRM")}

    def auth(authorization: str | None):
        if authorization != "Bearer other-secret":
            raise HTTPException(401, "unauthorized")

    @app.get("/api/v1/goods")
    def list_goods(authorization: str | None = Header(None)):
        auth(authorization)
        return {"result": {"goodsList": list(goods.values())}}

    @app.post("/api/v1/members")
    def join(body: dict, authorization: str | None = Header(None)):
        auth(authorization)
        return {"result": {"memberNo": 101, "email": body["memberEmail"]}}

    @app.post("/api/v1/orders")
    def place(body: dict, authorization: str | None = Header(None)):
        auth(authorization)
        total = 0
        for line in body["orderLines"]:
            g = goods[line["goodsNo"]]
            if not g["onSale"] or line["qty"] > g["inventory"]:
                raise HTTPException(409, "cannot order")
            g["inventory"] -= line["qty"]
            total += g["salePrice"]["amount"] * line["qty"]
        order_no = f"O-{len(orders) + 1:04d}"
        orders[order_no] = {"orderNo": order_no, "orderStatus": "ORDER_COMPLETE", "payment": {"total": total}}
        return {"result": orders[order_no]}

    @app.get("/api/v1/orders/{order_no}")
    def detail(order_no: str, authorization: str | None = Header(None)):
        auth(authorization)
        return {"result": orders[order_no]}

    @app.post("/api/v1/orders/{order_no}/{action}")
    def act(order_no: str, action: str, authorization: str | None = Header(None)):
        auth(authorization)
        before, after = flow[action]
        if orders[order_no]["orderStatus"] != before:
            raise HTTPException(409, f"{action} not allowed")
        orders[order_no]["orderStatus"] = after
        return {"result": orders[order_no]}

    return app


OTHER_SHOP_CONFIG = {
    "name": "다른 쇼핑몰", "environment": "staging", "base_url": "http://testserver",
    "auth": {"headers": {"Authorization": "Bearer ${OTHER_SHOP_TOKEN:-other-secret}"}},
    "statuses": {"PAID": "ORDER_COMPLETE", "PREPARING": "PREPARE", "SHIPPING": "DELIVERY_ING",
                 "DELIVERED": "DELIVERY_DONE", "CONFIRMED": "PURCHASE_CONFIRM"},
    "transitions": {"PAID": ["PREPARING"], "PREPARING": ["SHIPPING"], "SHIPPING": ["DELIVERED"],
                    "DELIVERED": ["CONFIRMED"], "CONFIRMED": []},
    "rules": {"free_shipping_threshold": 20000},
    "fields": {
        "product": {"id": "goodsNo", "name": "goodsNm", "price": "salePrice.amount", "stock": "inventory", "active": "onSale"},
        "order": {"id": "orderNo", "status": "orderStatus", "total": "payment.total"},
        "user": {"id": "memberNo"},
    },
    "endpoints": {
        "list_products": {"method": "GET", "path": "/api/v1/goods", "response_path": "result.goodsList"},
        "create_user": {"method": "POST", "path": "/api/v1/members", "body": {"memberEmail": "{email}"}, "response_path": "result"},
        "create_order": {"method": "POST", "path": "/api/v1/orders", "response_path": "result",
                         "body": {"memberNo": "{user_id}", "orderLines": "{items}"},
                         "item": {"goodsNo": "{product_id}", "qty": "{quantity}"}},
        "get_order": {"method": "GET", "path": "/api/v1/orders/{order_id}", "response_path": "result"},
        "change_status": {
            "PREPARING": {"method": "POST", "path": "/api/v1/orders/{order_id}/accept"},
            "SHIPPING": {"method": "POST", "path": "/api/v1/orders/{order_id}/ship"},
            "DELIVERED": {"method": "POST", "path": "/api/v1/orders/{order_id}/deliver"},
            "CONFIRMED": {"method": "POST", "path": "/api/v1/orders/{order_id}/confirm"},
        },
    },
}


@pytest.fixture
def other_shop():
    return HttpAdapter(load_config(OTHER_SHOP_CONFIG), client=TestClient(other_shop_app()))


def test_other_shop_orders_with_only_config(other_shop):
    """다른 모양의 서비스도 설정만으로: 주문 2건 → 구매확정, 상태는 표준값으로 읽히고 대상 상태값도 함께 남는다."""
    orders = engine.generate_orders(other_shop, 2, "basic", "CONFIRMED", seed=5)
    assert [o["status"] for o in orders] == ["CONFIRMED", "CONFIRMED"]
    assert orders[0]["target_status"] == "PURCHASE_CONFIRM"
    assert orders[0]["id"].startswith("O-")


def test_other_shop_skips_inactive_and_maps_nested_price(other_shop):
    """판매중지(onSale=false) 상품은 주문에 안 쓰고, 중첩 필드(salePrice.amount)도 매핑된다."""
    products = {p["id"]: p for p in other_shop.list_products()}
    assert products["G-1"]["price"] == 3000
    assert products["G-3"]["active"] is False
    orders = engine.generate_orders(other_shop, 3, "free_shipping", "PAID", seed=2)
    assert all(o["total"] >= 20000 for o in orders)


def test_other_shop_unsupported_features_are_explained(other_shop):
    """반품 흐름이 없는 서비스엔 반품 이동을, 쿠폰 API 가 없으면 쿠폰 시나리오를 이유와 함께 거부한다."""
    order = engine.generate_orders(other_shop, 1, "basic", "DELIVERED", seed=1)[0]
    with pytest.raises(ScenarioError, match="갈 수 없습니다"):
        engine.advance(other_shop, order["id"], "RETURNED")
    with pytest.raises(ScenarioError, match="issue_coupon"):
        engine.generate_orders(other_shop, 1, "coupon_fixed")


def test_other_shop_wrong_token(monkeypatch):
    """토큰이 틀리면 대상 서비스 401 이 그대로 보인다 (점검 단계에서 바로 발견)."""
    monkeypatch.setenv("OTHER_SHOP_TOKEN", "wrong")
    results = run_check(load_config(OTHER_SHOP_CONFIG), client=TestClient(other_shop_app()))
    assert any("HTTP 401" in message for ok, message in results if not ok)
