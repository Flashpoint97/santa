import asyncio

import structlog
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.v1 import router as v1_router
from app.config import settings
from app.db.session import engine
from app.log_config import configure_logging

logger = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Secret Santa API",
        version="0.1.0",
        # No public schema browser in production; the frontend is the only client.
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "production" else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Bearer tokens, not cookies: no credentialed CORS needed.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    app.include_router(v1_router)

    @app.get("/health", summary="Liveness + DB check", status_code=200)
    async def health() -> JSONResponse:
        try:
            async with asyncio.timeout(settings.db_health_timeout_s):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception:
            logger.warning("health_db_unavailable", exc_info=True)
            return JSONResponse(
                {"status": "degraded", "db": "down"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return JSONResponse({"status": "ok", "db": "ok"})

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
