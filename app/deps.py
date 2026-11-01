from fastapi import Request
from app.db import connect


def get_conn(request: Request):
    """요청마다 DB 연결을 하나 열고, 응답이 끝나면 닫는다 (FastAPI 의존성)."""
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()
