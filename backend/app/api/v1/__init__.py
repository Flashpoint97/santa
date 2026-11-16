from fastapi import APIRouter

from app.api.v1 import me, session

router = APIRouter(prefix="/api/v1")
router.include_router(session.router)
router.include_router(me.router)
