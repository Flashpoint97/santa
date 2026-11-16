"""Depends-able providers. Services are plain classes wired here; they never import FastAPI.

Session auth and the per-request store (bound to the verified participant) are added with
the code-login slice; there is deliberately no auth dependency yet, so no route can be
accidentally left open or half-protected.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str:
    # Behind Render's proxy, uvicorn --proxy-headers rewrites request.client from
    # X-Forwarded-For. We never parse that header ourselves (it's client-controlled).
    return request.client.host if request.client else "unknown"
