"""설정 파일만 보고 대상 서비스 API 를 호출하는 HTTP 어댑터.

엔진(engine.py)은 표준 형식(상품 {id, price, stock, max_per_order, active}, 주문 {id, status, total})만 안다.
대상 서비스마다 다른 주소 · 요청 본문 · 응답 구조 · 상태값을 표준 형식으로 바꾸는 게 이 파일의 일이다.

요청 본문 템플릿 규칙:
- 값이 정확히 "{name}" 이면 그 값을 타입 그대로 넣는다 (숫자 · 리스트 · null 유지)
- "{name}" 이 문자열 일부면 문자열로 끼워 넣는다
- 결과가 None 인 키는 본문에서 뺀다 (예: 쿠폰 없이 주문할 때 coupon_id)
"""
import re
from typing import Any

import httpx

from qa_target.config import ConfigError

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class TargetError(Exception):
    """대상 서비스 호출 실패 또는 응답이 설정과 맞지 않음."""


def render(template: Any, values: dict) -> Any:
    """요청 본문/경로 템플릿에 값을 채운다."""
    if isinstance(template, dict):
        rendered = {k: render(v, values) for k, v in template.items()}
        return {k: v for k, v in rendered.items() if v is not None}
    if isinstance(template, list):
        return [render(v, values) for v in template]
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template)
        if whole:
            return values.get(whole.group(1))
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)
    return template


def dig(data: Any, path: str | None) -> Any:
    """'data.order.id' 같은 점 경로로 응답 안의 값을 꺼낸다. 경로가 비면 data 그대로."""
    if not path:
        return data
    for key in path.split("."):
        if isinstance(data, list) and key.isdigit():
            data = data[int(key)]
        elif isinstance(data, dict) and key in data:
            data = data[key]
        else:
            raise TargetError(f"응답에 '{path}' 가 없습니다 ('{key}' 에서 멈춤).")
    return data


class HttpAdapter:
    """config 대로 대상 서비스를 호출한다. client 를 주입하면 그걸 쓴다 (테스트에서 FastAPI TestClient 등)."""

    def __init__(self, config: dict, client: httpx.Client | None = None):
        self.config = config
        self.endpoints = config["endpoints"]
        self.fields = config["fields"]
        self.to_target_status = config["statuses"]
        self.from_target_status = {str(v): k for k, v in config["statuses"].items()}
        self.client = client or httpx.Client(
            base_url=config["base_url"].rstrip("/"),
            headers=config["auth"].get("headers", {}),
            timeout=config.get("timeout_seconds", 10),
        )
        self._headers = config["auth"].get("headers", {}) if client is not None else {}

    def has(self, name: str) -> bool:
        return name in self.endpoints

    # ---------- 공통 호출 ----------

    def call(self, name: str, values: dict | None = None, spec: dict | None = None) -> Any:
        """endpoints.<name> 설정으로 요청을 보내고, response_path 아래 값을 돌려준다."""
        spec = spec or self.endpoints.get(name)
        if spec is None:
            raise ConfigError(f"endpoints.{name} 가 설정되지 않았습니다.")
        values = values or {}
        method = spec.get("method", "GET").upper()
        path = render(spec["path"], {k: v for k, v in values.items() if v is not None})
        body = render(spec["body"], values) if "body" in spec else None
        try:
            res = self.client.request(method, path, json=body, headers=self._headers)
        except httpx.HTTPError as e:
            raise TargetError(f"{name}: {method} {path} 연결 실패 ({e})") from None
        if res.status_code >= 400:
            raise TargetError(f"{name}: {method} {path} → HTTP {res.status_code} {res.text[:300]}")
        try:
            data = res.json() if res.content else None
        except ValueError:
            raise TargetError(f"{name}: 응답이 JSON 이 아닙니다: {res.text[:200]}") from None
        return dig(data, spec.get("response_path"))

    def _map(self, kind: str, raw: dict, required: tuple[str, ...]) -> dict:
        """대상 응답 객체를 fields.<kind> 매핑으로 표준 필드로 바꾼다. raw 원본도 같이 둔다."""
        mapping = self.fields.get(kind, {})
        result = {"raw": raw}
        for field, source in mapping.items():
            try:
                result[field] = dig(raw, source)
            except TargetError:
                if field in required:
                    raise TargetError(f"fields.{kind}.{field}='{source}' 를 응답에서 찾을 수 없습니다: {list(raw)[:10]}")
                result[field] = None
        for field in required:
            if field not in result:
                raise TargetError(f"fields.{kind}.{field} 매핑이 없습니다.")
        return result

    # ---------- 표준 동작 ----------

    def list_products(self) -> list[dict]:
        rows = self.call("list_products")
        if not isinstance(rows, list):
            raise TargetError("list_products 응답이 목록이 아닙니다 (response_path 확인).")
        products = []
        for row in rows:
            p = self._map("product", row, required=("id", "price", "stock"))
            p.setdefault("max_per_order", None)
            # active 매핑이 없으면 판매중으로 본다
            p["active"] = bool(p["active"]) if p.get("active") is not None else True
            products.append(p)
        return products

    def create_user(self, email: str, name: str) -> Any:
        return self._map("user", self.call("create_user", {"email": email, "name": name}), required=("id",))["id"]

    def issue_coupon(self, user_id, discount_type: str, value: int, **options) -> Any:
        types = self.config.get("coupon_types", {"FIXED": "FIXED", "PERCENT": "PERCENT"})
        values = {"user_id": user_id, "discount_type": types.get(discount_type, discount_type), "value": value,
                  "min_order_amount": options.get("min_order_amount"), "max_discount": options.get("max_discount"),
                  "expires_in_days": options.get("expires_in_days", 30)}
        return self._map("coupon", self.call("issue_coupon", values), required=("id",))["id"]

    def set_stock(self, product_id, stock: int) -> None:
        self.call("set_stock", {"product_id": product_id, "stock": stock})

    def create_order(self, user_id, items: list[dict], coupon_id=None) -> dict:
        item_template = self.endpoints["create_order"].get("item", {"product_id": "{product_id}", "quantity": "{quantity}"})
        rendered_items = [render(item_template, item) for item in items]
        raw = self.call("create_order", {"user_id": user_id, "items": rendered_items, "coupon_id": coupon_id})
        return self._order(raw)

    def get_order(self, order_id) -> dict:
        return self._order(self.call("get_order", {"order_id": order_id}))

    def change_status(self, order_id, status: str) -> None:
        """표준 상태로 한 단계 이동. change_status 설정은 상태별 엔드포인트 또는 "*"(공통) 하나."""
        specs = self.endpoints["change_status"]
        # path 가 바로 있으면 모든 상태에 같은 엔드포인트, 아니면 상태별 엔드포인트(없으면 "*")
        spec = specs if "path" in specs else (specs.get(status) or specs.get("*"))
        if spec is None:
            raise ConfigError(f"endpoints.change_status 에 {status} (또는 '*') 설정이 없습니다.")
        self.call("change_status", {"order_id": order_id, "status": self.to_target_status.get(status, status)}, spec)

    def _order(self, raw: dict) -> dict:
        order = self._map("order", raw, required=("id", "status"))
        target_status = str(order["status"])
        if target_status not in self.from_target_status:
            raise TargetError(f"대상 서비스 상태값 '{target_status}' 가 statuses 매핑에 없습니다.")
        order["target_status"] = target_status
        order["status"] = self.from_target_status[target_status]
        return order
