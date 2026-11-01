import re
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

QA_HEADERS = {"X-QA-Token": "local-qa-token"}
ALL_RULES = [f"R{n}" for n in range(1, 16)]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """임시 DB 로 앱을 띄우고 시드 데이터를 넣은 테스트 클라이언트."""
    monkeypatch.delenv("QA_TOKEN", raising=False)
    monkeypatch.delenv("QA_TOOLS_ENABLED", raising=False)
    client = TestClient(create_app(str(tmp_path / "test.db")))
    assert client.post("/qa/seed", headers=QA_HEADERS).status_code == 200
    return client


@pytest.fixture
def qa(client):
    """QA 도구 API 호출 헬퍼: qa("/orders/generate", {...})"""
    def _call(path, body=None):
        res = client.post(f"/qa{path}", json=body, headers=QA_HEADERS)
        assert res.status_code < 400, res.text
        return res.json()
    return _call


# ---------- 도메인 규칙 커버리지 리포트 ----------

_rule_counts: Counter = Counter()


def pytest_addoption(parser):
    parser.addoption("--rule", action="append", default=[], help="이 규칙 번호 테스트만 실행 (예: --rule R7 --rule R8)")


def _rules_of(item) -> set[str]:
    return {rule for mark in item.iter_markers(name="rule") for rule in mark.args}


def pytest_collection_modifyitems(config, items):
    """--rule 로 규칙별 테스트만 고르고, @pytest.mark.rule("R7") 마커를 모아 규칙별 테스트 수를 센다."""
    wanted = set(config.getoption("rule"))
    if wanted:
        selected = [item for item in items if _rules_of(item) & wanted]
        config.hook.pytest_deselected(items=[item for item in items if item not in selected])
        items[:] = selected
    _rule_counts.clear()
    for item in items:
        for rule in _rules_of(item):
            _rule_counts[rule] += 1


def pytest_terminal_summary(terminalreporter):
    """테스트 실행 끝에 SPEC 규칙(R1~R15)별 테스트 수를 출력하고, 테스트가 없는 규칙을 경고한다."""
    if not _rule_counts:
        return
    terminalreporter.section("도메인 규칙 커버리지 (docs/SPEC.md)")
    # --rule 로 골라 돌렸으면 고른 규칙만 보여준다
    wanted = terminalreporter.config.getoption("rule")
    for rule in [r for r in ALL_RULES if r in wanted] or ALL_RULES:
        count = _rule_counts.get(rule, 0)
        terminalreporter.write_line(f"{rule:>4}: {count:>3}개" + ("  <- 테스트 없음" if count == 0 else ""))
    unknown = sorted(r for r in _rule_counts if not re.fullmatch(r"R(1[0-5]|[1-9])", r))
    if unknown:
        terminalreporter.write_line(f"SPEC 에 없는 규칙 번호: {unknown}")
