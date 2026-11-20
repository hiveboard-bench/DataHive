"""Camera handling: slice one episode out of a source shard, re-encode, verify.

Sources that pack many episodes into one MP4 need a segment extracted per
episode. ``datahive validate`` requires 180-1280 px sides, a decoder OpenCV can
open, per-camera fps agreement, and a video duration within 0.5 s of the
episode. We therefore transcode to H.264/yuv420p and force the extracted
segment to contain exactly as many frames as the source trajectory.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_VIDEO_SIDE_PX = 180
MAX_VIDEO_SIDE_PX = 1280
DEFAULT_CRF = 19
DEFAULT_PRESET = "medium"

_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Video: ([A-Za-z0-9_]+)")
_SIZE_RE = re.compile(r"(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"([\d.]+)\s+fps")
_FRAMES_RE = re.compile(r"frame=\s*(\d+)")

_ffmpeg: str | None = None


class VideoError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    codec: str
    width: int
    height: int
    fps: float
    frames: int

    @property
    def duration(self) -> float:
        return self.frames / self.fps if self.fps else 0.0


def find_ffmpeg() -> str:
    """The ffmpeg bundled with imageio-ffmpeg; no system install is required."""
    global _ffmpeg
    if _ffmpeg:
        return _ffmpeg
    try:
        import imageio_ffmpeg
    except ImportError as err:  # pragma: no cover - dependency gate
        raise VideoError(
            "imageio-ffmpeg is not installed. Run: "
            'pip install "datahive-tools[converter]"'
        ) from err
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    if not Path(exe).exists():
        raise VideoError(f"ffmpeg binary missing at {exe}")
    _ffmpeg = exe
    return exe


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # Never shell=True: the repository path contains a space.
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def _parse_stream(log: str, path: Path) -> tuple[str, int, int, float]:
    codec_match = _STREAM_RE.search(log)
    size_match = _SIZE_RE.search(log)
    fps_match = _FPS_RE.search(log)
    if not (codec_match and size_match and fps_match):
        raise VideoError(f"could not read video metadata from {path}:\n{log[-800:]}")
    return (
        codec_match.group(1),
        int(size_match.group(1)),
        int(size_match.group(2)),
        float(fps_match.group(1)),
    )


def _cache_key(path: Path) -> tuple:
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns)


_header_cache: dict[tuple, MediaInfo] = {}


def probe_header(path: Path) -> MediaInfo:
    """Codec, size and fps from the container header only.

    Deliberately does not decode: a source shard can hold an entire dataset's
    video, and decoding it once per episode would dominate the run time.
    ``frames`` is left as 0 because it is unknown without decoding.
    """
    path = Path(path)
    if not path.exists():
        raise VideoError(f"video not found: {path}")
    key = _cache_key(path)
    if key in _header_cache:
        return _header_cache[key]
    proc = _run([find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)])
    codec, width, height, fps = _parse_stream(proc.stderr, path)
    info = MediaInfo(codec=codec, width=width, height=height, fps=fps, frames=0)
    _header_cache[key] = info
    return info


_media_cache: dict[tuple, MediaInfo] = {}


def probe_media(path: Path) -> MediaInfo:
    """Codec, size, fps and frame count. Decodes the whole file -- use on the
    small extracted segments, not on multi-hundred-megabyte source shards."""
    path = Path(path)
    if not path.exists():
        raise VideoError(f"video not found: {path}")
    key = _cache_key(path)
    if key in _media_cache:
        return _media_cache[key]
    proc = _run([find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path),
                 "-map", "0:v:0", "-f", "null", "-"])
    codec, width, height, fps = _parse_stream(proc.stderr, path)
    frames = [int(m) for m in _FRAMES_RE.findall(proc.stderr)]
    if not frames:
        raise VideoError(f"could not count frames in {path}:\n{proc.stderr[-800:]}")
    info = MediaInfo(codec=codec, width=width, height=height, fps=fps, frames=frames[-1])
    _media_cache[key] = info
    return info


def _scale_filter(width: int, height: int, fps: float) -> str:
    filters = [f"fps={fps}"]
    if max(width, height) > MAX_VIDEO_SIDE_PX:
        filters.append(
            f"scale='min({MAX_VIDEO_SIDE_PX},iw)':'min({MAX_VIDEO_SIDE_PX},ih)'"
            ":force_original_aspect_ratio=decrease"
        )
    # yuv420p needs even dimensions.
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return ",".join(filters)


def extract_segment(
    source: Path,
    dest: Path,
    *,
    start_frame: int,
    frame_count: int,
    fps: float,
    mode: str = "transcode",
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    media: MediaInfo | None = None,
) -> MediaInfo:
    """Write ``frame_count`` frames starting at ``start_frame`` to ``dest``.

    Transcodes to H.264/yuv420p by default. ``mode="copy"`` stream-copies the
    source codec, which is much faster but may produce AV1 that OpenCV cannot
    decode and gives frame-accurate seeking up to the keyframe only; it is for
    debugging, not for submission.
    """
    if frame_count <= 0:
        raise VideoError(f"refusing to extract {frame_count} frames")
    source, dest = Path(source), Path(dest)
    media = media or probe_header(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    seek = max(0.0, start_frame / media.fps)

    def build(pad: bool) -> list[str]:
        cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-ss", f"{seek:.6f}", "-i", str(source), "-map", "0:v:0", "-an"]
        if mode == "copy":
            cmd += ["-c:v", "copy"]
        else:
            vf = _scale_filter(media.width, media.height, fps)
            if pad:
                vf += ",tpad=stop_mode=clone:stop=-1"
            cmd += ["-vf", vf, "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd += ["-frames:v", str(frame_count), str(dest)]
        return cmd

    proc = _run(build(pad=False))
    if proc.returncode != 0 or not dest.exists():
        raise VideoError(f"ffmpeg failed for {source} frames {start_frame}..{start_frame + frame_count}:\n{proc.stderr[-1200:]}")

    info = probe_media(dest)
    if info.frames < frame_count:
        shortfall = frame_count - info.frames
        tolerance = max(5, int(0.1 * frame_count))
        if mode == "copy" or shortfall > tolerance:
            raise VideoError(
                f"{dest.name}: extracted {info.frames} frames, expected {frame_count} "
                f"(shortfall {shortfall} exceeds tolerance {tolerance})"
            )
        proc = _run(build(pad=True))
        if proc.returncode != 0:
            raise VideoError(f"ffmpeg padding failed for {dest:\n}{proc.stderr[-1200:]}")
        info = probe_media(dest)
        if info.frames != frame_count:
            raise VideoError(f"{dest.name}: padded to {info.frames} frames, expected {frame_count}")

    if max(info.width, info.height) > MAX_VIDEO_SIDE_PX:
        raise VideoError(f"{dest.name}: still {info.width}x{info.height} after scaling")
    if min(info.width, info.height) < MIN_VIDEO_SIDE_PX:
        raise VideoError(f"{dest.name}: {info.width}x{info.height} is below the {MIN_VIDEO_SIDE_PX}px minimum")
    return info


def ensure_browser_playable(path: Path) -> None:
    info = probe_media(path)
    if info.codec not in {"h264"}:
        raise VideoError(
            f"{path.name} is {info.codec}; submission videos must be H.264. "
            "Re-run without --video-mode copy."
        )


def stage_path(scratch: Path, camera: str) -> Path:
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch / f"{camera}.mp4"
