import pytest
from fastapi.testclient import TestClient

from app.main import create_app

QA_HEADERS = {"X-QA-Token": "local-qa-token"}


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
