"""웹 화면 E2E 공통 픽스처.

실제 uvicorn 서버를 임시 DB + 빈 포트로 띄우고(세션당 1번), 테스트마다 QA 도구 seed 로 데이터를 초기화한다.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

QA_HEADERS = {"X-QA-Token": "local-qa-token"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """테스트 전용 서버를 띄우고 URL 을 돌려준다. 끝나면 종료."""
    port = _free_port()
    env = {
        **os.environ,
        "DB_PATH": str(tmp_path_factory.mktemp("e2e") / "e2e.db"),
        "QA_TOKEN": "local-qa-token",
        "DEMO_MODE": "false",
        "QA_TOOLS_ENABLED": "true",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", str(port)],
        cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if requests.get(f"{url}/products", timeout=1).status_code == 200:
                break
        except requests.ConnectionError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("E2E 서버가 20초 안에 뜨지 않았습니다.")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def base_url(server):
    """pytest-playwright 의 page.goto("/") 가 테스트 서버를 보도록 base_url 을 덮어쓴다."""
    return server


@pytest.fixture
def qa(server):
    """QA 도구 API 로 사전조건을 만든다: qa("POST", "/orders/generate", {...})."""
    def _call(method, path, body=None):
        res = requests.request(method, f"{server}/qa{path}", json=body, headers=QA_HEADERS, timeout=10)
        assert res.status_code < 400, res.text
        return res.json()
    return _call


@pytest.fixture(autouse=True)
def fresh_data(qa):
    """테스트마다 기본 데이터로 초기화 (테스트 간 데이터 의존 제거)."""
    qa("POST", "/seed")


@pytest.fixture
def app_page(page):
    """웹 화면을 열고 상품 표가 그려질 때까지 기다린 페이지."""
    page.goto("/")
    page.get_by_test_id("product-row-1").wait_for()
    return page
