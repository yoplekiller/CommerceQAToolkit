"""실행: uvicorn app.main:create_app --factory --reload"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import connect, get_db_path, init_schema
from app.routers import qa, service
from app.services import DomainError


def create_app(db_path: str | None = None) -> FastAPI:
    """FastAPI 앱을 만든다.

    db_path 를 주면 그 파일을 쓰고(테스트에서 임시 DB), 없으면 DB_PATH 환경변수를 쓴다.
    """
    app = FastAPI(title="Commerce QA Toolkit", version="0.1.0")
    app.state.db_path = db_path or get_db_path()

    conn = connect(app.state.db_path)
    init_schema(conn)
    conn.close()

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        """DomainError 를 {"error": {"code", "message"}} 형식의 JSON 응답으로 바꾼다."""
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    app.include_router(service.router)
    app.include_router(qa.router)
    return app
