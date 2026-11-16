"""GIF -> MP4 conversion and `GET me/receiver/animation` (synthetic data only)."""

import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import imageio_ffmpeg
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select

from app.db.models import Animation, Assignment
from app.db.session import SessionLocal, engine
from app.services.animation import AnimationError, convert_gif_to_mp4, read_gif
from app.services.drawing import run_draw
from tests.conftest import Seeded, seed_event
from tests.gifs import make_gif

ANIMATION = "/api/v1/me/receiver/animation"


# --- conversion ----------------------------------------------------------------------------


def probe(video: bytes, tmp_path: Path) -> str:
    """ffmpeg's own description of the file (it lists the streams on stderr)."""
    path = tmp_path / "probe.mp4"
    path.write_bytes(video)
    result = subprocess.run(  # noqa: S603 - fixed argv, bundled binary, test-only
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stderr


def dimensions(description: str) -> tuple[int, int]:
    match = re.search(r"Video: .*?, (\d+)x(\d+)", description)
    assert match
    return int(match[1]), int(match[2])


def test_a_tiny_gif_becomes_a_valid_muted_h264_mp4(tmp_path: Path) -> None:
    video = convert_gif_to_mp4(make_gif())
    assert video[4:8] == b"ftyp"  # the MP4 container signature
    info = probe(video, tmp_path)
    assert "Video: h264" in info
    assert "yuv420p" in info
    assert "Audio:" not in info
    # +faststart: the index (moov) precedes the media data so playback can start early.
    assert video.find(b"moov") < video.find(b"mdat")


def test_odd_sides_are_made_even_and_small_clips_are_not_scaled_up(tmp_path: Path) -> None:
    width, height = dimensions(probe(convert_gif_to_mp4(make_gif(15, 13)), tmp_path))
    assert (width % 2, height % 2) == (0, 0)
    assert width <= 15
    assert height <= 13


def test_large_clips_are_scaled_to_fit_720_on_the_long_side(tmp_path: Path) -> None:
    width, height = dimensions(probe(convert_gif_to_mp4(make_gif(1000, 500, frames=2)), tmp_path))
    assert (width, height) == (720, 360)
    width, height = dimensions(probe(convert_gif_to_mp4(make_gif(500, 1001, frames=2)), tmp_path))
    assert max(width, height) <= 720
    assert (width % 2, height % 2) == (0, 0)


@pytest.mark.parametrize(
    "data",
    [b"", b"not a gif at all", b"\x89PNG\r\n\x1a\n" + b"0" * 32, b"GIF8" + b"0" * 32],
)
def test_non_gif_input_is_refused_before_ffmpeg_runs(data: bytes) -> None:
    with pytest.raises(AnimationError):
        convert_gif_to_mp4(data)


def test_a_gif_header_with_garbage_behind_it_fails_cleanly() -> None:
    with pytest.raises(AnimationError):
        convert_gif_to_mp4(b"GIF89a" + b"\x00" * 64)


def test_read_gif_enforces_the_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "big.gif"
    path.write_bytes(make_gif())
    assert read_gif(path)[:6] == b"GIF89a"
    with pytest.raises(AnimationError, match="larger"):
        read_gif(path, max_bytes=10)
    with pytest.raises(AnimationError, match="not found"):
        read_gif(tmp_path / "missing.gif")


# --- the route -----------------------------------------------------------------------------


def fake_video(name: str) -> bytes:
    """Not a playable clip: just distinctive bytes with an MP4-looking header."""
    return b"\x00\x00\x00\x18ftypmp42" + f"clip-of-{name}".encode()


async def seed_with_animations() -> Seeded:
    seeded = await seed_event()
    names = tuple(seeded.participant_ids)
    async with SessionLocal() as s:
        s.add_all(
            Animation(
                participant_id=seeded.participant_ids[n],
                video=fake_video(n),
                content_type="video/mp4",
                byte_size=len(fake_video(n)),
            )
            for n in names
        )
        await s.commit()
    return seeded


async def draw_now(seeded: Seeded) -> dict[str, str]:
    """Run the draw and return giver name -> receiver name (read straight from the database)."""
    async with SessionLocal() as s:
        await run_draw(s, redraw=False)
    async with SessionLocal() as s:
        rows = (await s.scalars(select(Assignment))).all()
    names = {v: k for k, v in seeded.participant_ids.items()}
    return {names[a.giver_id]: names[a.receiver_id] for a in rows}


async def drawn_world() -> tuple[Seeded, dict[str, str]]:
    seeded = await seed_with_animations()
    return seeded, await draw_now(seeded)


async def login(client: httpx.AsyncClient, code: str) -> dict[str, str]:
    r = await client.post("/api/v1/session", json={"code": code})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_each_participant_gets_only_their_own_receivers_video(
    client: httpx.AsyncClient,
) -> None:
    seeded, receivers = await drawn_world()
    for giver, receiver in receivers.items():
        headers = await login(client, seeded.codes[giver])
        response = await client.get(ANIMATION, headers=headers)
        assert response.status_code == 200
        assert response.content == fake_video(receiver)
        for other in seeded.participant_ids:
            if other != receiver:
                assert response.content != fake_video(other)


async def test_response_headers(client: httpx.AsyncClient) -> None:
    seeded, _ = await drawn_world()
    headers = await login(client, seeded.codes["Person A"])
    response = await client.get(ANIMATION, headers=headers)
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(len(response.content))
    assert response.content[4:8] == b"ftyp"


async def test_the_route_takes_no_id(client: httpx.AsyncClient) -> None:
    seeded, receivers = await drawn_world()
    headers = await login(client, seeded.codes["Person A"])
    someone_else = seeded.participant_ids["Person B"]
    for path in (f"{ANIMATION}/{someone_else}", f"/api/v1/animations/{someone_else}"):
        assert (await client.get(path, headers=headers)).status_code == 404
    # A query-string id is ignored: the receiver still comes from the session's own Assignment.
    response = await client.get(
        ANIMATION, params={"participant_id": str(someone_else)}, headers=headers
    )
    assert response.content == fake_video(receivers["Person A"])


@pytest.mark.parametrize("auth", [None, "Bearer nonsense", "Basic abc", "Bearer "])
async def test_no_valid_session_is_401(client: httpx.AsyncClient, auth: str | None) -> None:
    await drawn_world()
    headers = {"Authorization": auth} if auth else {}
    response = await client.get(ANIMATION, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "ftyp" not in response.text


async def test_404_before_the_draw(client: httpx.AsyncClient) -> None:
    seeded = await seed_with_animations()
    headers = await login(client, seeded.codes["Person A"])
    assert (await client.get(ANIMATION, headers=headers)).status_code == 404


async def test_404_when_the_receiver_has_no_animation(client: httpx.AsyncClient) -> None:
    seeded, receivers = await drawn_world()
    async with SessionLocal() as s:
        row = await s.scalar(
            select(Animation).where(
                Animation.participant_id == seeded.participant_ids[receivers["Person A"]]
            )
        )
        assert row is not None
        await s.delete(row)
        await s.commit()
    headers = await login(client, seeded.codes["Person A"])
    assert (await client.get(ANIMATION, headers=headers)).status_code == 404
    # Everyone else still gets theirs.
    other = next(n for n in receivers if n != "Person A" and receivers[n] != receivers["Person A"])
    headers = await login(client, seeded.codes[other])
    assert (await client.get(ANIMATION, headers=headers)).status_code == 200


@contextmanager
def captured_sql() -> Iterator[list[str]]:
    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa_event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_only_the_animation_route_reads_the_video_blob(client: httpx.AsyncClient) -> None:
    seeded, _ = await drawn_world()
    with captured_sql() as statements:
        headers = await login(client, seeded.codes["Person A"])
        assert (await client.get("/api/v1/me", headers=headers)).status_code == 200
        assert (await client.post("/api/v1/me/reveal", headers=headers)).status_code == 200
    assert not [sql for sql in statements if "animations" in sql]
    with captured_sql() as statements:
        assert (await client.get(ANIMATION, headers=headers)).status_code == 200
    assert any("animations.video" in sql for sql in statements)
