"""대상 서비스 설정(YAML) 읽기.

- `${ENV}` / `${ENV:-기본값}` 은 환경변수로 바꾼다 (토큰 같은 비밀값은 파일에 쓰지 않기 위해).
- 값이 비어 있거나 `TODO` 가 들어 있으면 "아직 안 채운 칸" 으로 본다 → find_blanks 로 목록화.
- environment 가 운영(production/prod/live)이면 읽는 단계에서 거부한다. QA 도구는 데이터를 만들고 상태를 강제로 바꾸므로.
"""
import os
import re
from pathlib import Path

import yaml

STANDARD_STATUSES = [
    "PAID", "PREPARING", "SHIPPING", "DELIVERED", "CONFIRMED", "CANCELED", "RETURN_REQUESTED", "RETURNED",
]
# 대상 서비스가 transitions 를 안 주면 쓰는 기본 주문 흐름 (이 레포 SPEC 과 같음)
DEFAULT_TRANSITIONS = {
    "PAID": ["PREPARING", "CANCELED"],
    "PREPARING": ["SHIPPING", "CANCELED"],
    "SHIPPING": ["DELIVERED"],
    "DELIVERED": ["CONFIRMED", "RETURN_REQUESTED"],
    "RETURN_REQUESTED": ["RETURNED"],
    "CONFIRMED": [], "CANCELED": [], "RETURNED": [],
}
REQUIRED_ENDPOINTS = ["list_products", "create_order", "get_order", "change_status"]
OPTIONAL_ENDPOINTS = ["create_user", "issue_coupon", "set_stock"]
BLOCKED_ENVIRONMENTS = {"production", "prod", "live", "운영"}

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """설정 파일을 쓸 수 없음 (문법 오류, 필수 항목 없음, 운영 환경 등)."""


def _substitute_env(value, missing: list[str]):
    """문자열 안의 ${VAR} / ${VAR:-default} 를 환경변수로 바꾼다. 없는 변수는 missing 에 모으고 빈 값으로 둔다."""
    if isinstance(value, dict):
        return {k: _substitute_env(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v, missing) for v in value]
    if not isinstance(value, str):
        return value

    def replace(match):
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        missing.append(name)
        return ""
    return _ENV_PATTERN.sub(replace, value)


def find_blanks(value, path: str = "") -> list[str]:
    """빈 문자열이거나 TODO 가 들어 있는 칸의 위치(점 경로)를 모은다."""
    if isinstance(value, dict):
        return [b for k, v in value.items() for b in find_blanks(v, f"{path}.{k}" if path else str(k))]
    if isinstance(value, list):
        return [b for i, v in enumerate(value) for b in find_blanks(v, f"{path}[{i}]")]
    if isinstance(value, str) and (value.strip() == "" or "TODO" in value):
        return [path]
    return []


def load_config(source: str | Path | dict) -> dict:
    """설정 파일(또는 dict)을 읽어 환경변수를 채우고 기본값을 붙인다.

    결과 dict 에 `_missing_env`(없는 환경변수), `_blanks`(안 채운 칸)를 함께 넣는다.
    운영 환경이면 ConfigError.
    """
    if isinstance(source, dict):
        raw = source
    else:
        try:
            raw = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(f"설정 파일이 없습니다: {source}") from None
        except yaml.YAMLError as e:
            raise ConfigError(f"YAML 문법 오류: {e}") from None
    if not isinstance(raw, dict):
        raise ConfigError("설정 파일 최상위는 key: value 형식이어야 합니다.")

    missing_env: list[str] = []
    config = _substitute_env(raw, missing_env)

    environment = str(config.get("environment", "")).strip().lower()
    if environment in BLOCKED_ENVIRONMENTS:
        raise ConfigError(
            f"environment={config['environment']} : QA 도구는 운영 환경에 쓰지 않습니다 (테스트 데이터 생성 · 상태 강제 변경). "
            "스테이징/개발 서버 주소를 넣으세요."
        )

    config.setdefault("auth", {})
    config.setdefault("endpoints", {})
    config.setdefault("fields", {})
    config.setdefault("rules", {})
    config["rules"].setdefault("free_shipping_threshold", 0)
    config["rules"].setdefault("max_quantity", 99)
    config.setdefault("transitions", DEFAULT_TRANSITIONS)
    # statuses 를 안 주면 대상 서비스도 표준 상태값을 그대로 쓴다고 본다
    config.setdefault("statuses", {s: s for s in STANDARD_STATUSES})
    config["_missing_env"] = sorted(set(missing_env))
    config["_blanks"] = find_blanks({k: v for k, v in config.items() if not k.startswith("_")})
    return config


def validate(config: dict) -> list[str]:
    """실행에 꼭 필요한 항목이 빠졌는지 검사해 문제 목록을 돌려준다 (비어 있으면 OK)."""
    problems = []
    if not config.get("base_url"):
        problems.append("base_url 이 비어 있습니다.")
    for name in REQUIRED_ENDPOINTS:
        if name not in config["endpoints"]:
            problems.append(f"endpoints.{name} 가 없습니다 (필수).")
    unknown = set(config["statuses"]) - set(STANDARD_STATUSES)
    if unknown:
        problems.append(f"statuses 에 표준 상태가 아닌 키가 있습니다: {sorted(unknown)} (키는 표준 상태, 값은 대상 서비스 상태값)")
    for status, nexts in config["transitions"].items():
        for nxt in nexts:
            if nxt not in config["transitions"]:
                problems.append(f"transitions.{status} → {nxt} : {nxt} 가 transitions 에 없습니다.")
    for blank in config["_blanks"]:
        problems.append(f"아직 안 채운 칸: {blank}")
    for name in config["_missing_env"]:
        problems.append(f"환경변수가 없습니다: {name}")
    return problems
