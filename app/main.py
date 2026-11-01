"""실행: uvicorn app.main:create_app --factory --reload"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import connect, get_db_path, init_schema
from app.routers import qa, service
from app.services import DomainError


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Delivery QA Toolkit", version="0.1.0")
    app.state.db_path = db_path or get_db_path()

    conn = connect(app.state.db_path)
    init_schema(conn)
    conn.close()

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    app.include_router(service.router)
    app.include_router(qa.router)
    return app
