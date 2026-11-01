"""실행: uvicorn app.main:create_app --factory --reload"""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app import qa_tools, services
from app.db import connect, get_db_path, init_schema
from app.routers import qa, service
from app.services import DomainError

STATIC_DIR = Path(__file__).parent / "static"


def is_demo_mode() -> bool:
    """공개 데모 배포 여부. 켜져 있으면 웹 화면이 QA 토큰을 자동으로 채운다 (데모 데이터라 공개해도 되는 경우만)."""
    return os.getenv("DEMO_MODE", "false").lower() == "true"


def create_app(db_path: str | None = None) -> FastAPI:
    """FastAPI 앱을 만든다.

    db_path 를 주면 그 파일을 쓰고(테스트에서 임시 DB), 없으면 DB_PATH 환경변수를 쓴다.
    AUTO_SEED=true 면 상품이 하나도 없을 때 시드 데이터를 넣는다 (재시작마다 DB 가 비는 무료 호스팅용).
    """
    app = FastAPI(title="Commerce QA Toolkit", version="0.2.0")
    app.state.db_path = db_path or get_db_path()

    conn = connect(app.state.db_path)
    init_schema(conn)
    if os.getenv("AUTO_SEED", "false").lower() == "true" and not conn.execute("SELECT 1 FROM products").fetchone():
        qa_tools.seed(conn)
    conn.close()

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        """DomainError 를 {"error": {"code", "message"}} 형식의 JSON 응답으로 바꾼다."""
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    @app.get("/", include_in_schema=False)
    def index():
        """QA 도구 웹 화면"""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/config", include_in_schema=False)
    def config():
        """웹 화면 설정. 상태 전이표(이동 가능한 상태만 버튼으로 보여주기용)와, 데모 모드일 때만 QA 토큰을 내려준다."""
        return {
            "demo_mode": is_demo_mode(),
            "qa_token": os.getenv("QA_TOKEN", "local-qa-token") if is_demo_mode() else None,
            "statuses": services.STATUSES,
            "transitions": {k: sorted(v) for k, v in services.ALLOWED_TRANSITIONS.items()},
        }

    app.include_router(service.router)
    app.include_router(qa.router)
    return app
