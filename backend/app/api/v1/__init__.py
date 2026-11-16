from fastapi import APIRouter

# Routes arrive with the code-login slice; the router and its prefix are the stable mount point.
router = APIRouter(prefix="/api/v1")
