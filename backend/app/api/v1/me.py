from fastapi import APIRouter, HTTPException, status

from app.dependencies import Store
from app.schemas.me import MeResponse

router = APIRouter(tags=["me"])


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=MeResponse,
    summary="The caller's Countdown data",
    responses={401: {"description": "Missing, unknown or expired token"}},
)
async def get_me(store: Store) -> MeResponse:
    me = await store.get_me()
    if me is None:
        # A session whose participant vanished (wipe/replace race): fail closed.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return me
