from fastapi import APIRouter, HTTPException, Response, status

from app.dependencies import Store
from app.schemas.me import MeResponse, RevealResponse

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


@router.post(
    "/me/reveal",
    status_code=status.HTTP_200_OK,
    response_model=RevealResponse,
    summary="Reveal who the caller gives a gift to",
    responses={
        401: {"description": "Missing, unknown or expired token"},
        404: {"description": "The draw has not happened yet"},
    },
)
async def reveal(store: Store, response: Response) -> RevealResponse:
    result = await store.reveal()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The draw has not happened yet")
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get(
    "/me/receiver/animation",
    status_code=status.HTTP_200_OK,
    response_model=None,
    response_class=Response,
    summary="The looping video of the person the caller gives a gift to",
    responses={
        200: {"content": {"video/mp4": {}}, "description": "The receiver's muted MP4"},
        401: {"description": "Missing, unknown or expired token"},
        404: {"description": "No draw yet, or no animation stored"},
    },
)
async def get_receiver_animation(store: Store) -> Response:
    # No id in the path: the receiver comes from the caller's own Assignment, so there is
    # nothing to enumerate and another participant's clip is unreachable.
    animation = await store.receiver_animation()
    if animation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No animation available")
    return Response(
        content=animation.video,
        media_type=animation.content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
