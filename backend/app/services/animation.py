"""Turn the organizer's GIFs into small phone-friendly MP4s. No FastAPI imports, no database.

Runs in the CLI before any database transaction opens: converting is slow and can fail, and a
bad GIF must stop the load before a single row is written. ffmpeg comes from the
`imageio-ffmpeg` wheel (a bundled static binary), so nothing has to be installed system-wide.
Error messages name the problem, never the file's name or contents.
"""

import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
import structlog

from app.services.list_file import ListFile, ListFileError

logger = structlog.get_logger(__name__)

MAX_GIF_BYTES = 30 * 1024 * 1024
MAX_LONG_SIDE = 720
CONVERT_TIMEOUT_S = 180
VIDEO_CONTENT_TYPE = "video/mp4"
_GIF_MAGIC = (b"GIF87a", b"GIF89a")

# Fit inside a 720x720 box without ever scaling up, with both sides even (yuv420p needs it).
# The quotes keep the commas inside min() from being read as filter separators.
_SCALE = (
    f"scale=w='min({MAX_LONG_SIDE},iw)':h='min({MAX_LONG_SIDE},ih)'"
    ":force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"
)


class AnimationError(Exception):
    """A GIF cannot be used. The message is safe to print."""


def check_gif(data: bytes, *, max_bytes: int = MAX_GIF_BYTES) -> None:
    """Raise AnimationError unless `data` looks like a GIF within the size limit."""
    if not data:
        raise AnimationError("file is empty")
    if len(data) > max_bytes:
        raise AnimationError(f"file is larger than {max_bytes // (1024 * 1024)} MB")
    if not data.startswith(_GIF_MAGIC):
        raise AnimationError("file is not a GIF")


def read_gif(path: Path, *, max_bytes: int = MAX_GIF_BYTES) -> bytes:
    """Read a GIF from disk without ever loading more than the limit, then validate it."""
    try:
        if not path.is_file():
            raise AnimationError("file not found")
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        raise AnimationError("file cannot be read") from None
    check_gif(data, max_bytes=max_bytes)
    return data


def convert_gif_to_mp4(gif: bytes) -> bytes:
    """Muted H.264 MP4: yuv420p, even sides, at most 720px on the long side, faststart."""
    check_gif(gif)
    with tempfile.TemporaryDirectory(prefix="santa-anim-") as tmp:
        source, target = Path(tmp) / "in.gif", Path(tmp) / "out.mp4"
        source.write_bytes(gif)
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source),
            "-an",  # no audio track: the clip is muted by construction
            "-vf",
            _SCALE,
            "-c:v",
            "libx264",
            "-profile:v",
            "main",  # widest phone support
            "-preset",
            "medium",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",  # moov atom first, so playback can start before the end downloads
            str(target),
        ]
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell, our own temp paths
                command, capture_output=True, timeout=CONVERT_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired:
            raise AnimationError("conversion timed out") from None
        except OSError:
            raise AnimationError("the bundled ffmpeg could not be started") from None
        if result.returncode != 0 or not target.is_file():
            # ffmpeg's stderr is logged by length only; it can quote the (private) input.
            logger.warning("gif_conversion_failed", returncode=result.returncode)
            raise AnimationError("conversion failed (is it a valid GIF?)")
        video = target.read_bytes()
    if not video:
        raise AnimationError("conversion produced an empty video")
    return video


def prepare_animations(parsed: ListFile, base_dir: Path) -> dict[str, bytes]:
    """Validate and convert every participant's GIF; key is the casefolded display_name.

    `gif` paths are relative to the list file's directory. Every problem across all
    participants is collected into one ListFileError, and nothing here touches the database.
    """
    problems: list[str] = []
    sources: dict[str, bytes] = {}
    for i, entry in enumerate(parsed.participants):
        where = f"participants[{i}].gif"
        path = Path(entry.gif)
        if path.is_absolute():
            problems.append(f"{where}: must be relative to the list file's directory")
            continue
        try:
            sources[entry.display_name.casefold()] = read_gif(base_dir / path)
        except AnimationError as exc:
            problems.append(f"{where}: {exc}")
    if problems:
        raise ListFileError(problems)

    videos: dict[str, bytes] = {}
    for i, entry in enumerate(parsed.participants):
        key = entry.display_name.casefold()
        try:
            videos[key] = convert_gif_to_mp4(sources[key])
        except AnimationError as exc:
            problems.append(f"participants[{i}].gif: {exc}")
    if problems:
        raise ListFileError(problems)
    logger.info("animations_converted", count=len(videos))
    return videos
