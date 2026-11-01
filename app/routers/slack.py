"""Slack 슬래시 커맨드 수신 엔드포인트.

SLACK_SIGNING_SECRET 이 없거나 QA 도구가 꺼져 있으면 404 (기능 자체가 없는 것처럼).
Slack 은 3초 안에 응답을 받아야 해서, 명령은 동기로 바로 처리한다 (SQLite 라 수십 ms).
"""
import os
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request

from app import slack_bot
from app.deps import get_conn

router = APIRouter(tags=["slack"])


@router.post("/slack/commands")
async def slash_command(request: Request, conn=Depends(get_conn)):
    """Slack 슬래시 커맨드(/qa) 처리. 서명이 맞지 않으면 401."""
    secret = slack_bot.get_signing_secret()
    if secret is None or os.getenv("QA_TOOLS_ENABLED", "true").lower() != "true":
        raise HTTPException(status_code=404, detail="Not Found")

    # 서명은 받은 바이트 그대로 계산해야 해서 폼 파싱 전에 원본 body 를 읽는다
    body = await request.body()
    if not slack_bot.verify_signature(
        secret,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        body,
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(status_code=401, detail="Slack 서명이 올바르지 않습니다.")

    form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
    base_url = os.getenv("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")
    return slack_bot.handle(conn, form.get("text", ""), base_url)
