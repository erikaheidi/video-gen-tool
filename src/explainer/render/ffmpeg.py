"""ffmpeg command construction (pure functions returning argv lists) and runners."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .scene import SceneLayers

ZOOM_AMOUNT = 0.12   # zoom-in/out scale change over a scene
PAN_EXTRA = 0.15     # extra width used for panning


class FFmpegError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegError("ffmpeg not found on PATH (install it, e.g. `apk add ffmpeg` or `brew install ffmpeg`)")
    return path


@dataclass(frozen=True)
class Encoding:
    preset: str = "medium"
    crf: int = 20

    @classmethod
    def final(cls, preview: bool = False) -> "Encoding":
        return cls("ultrafast", 28) if preview else cls("medium", 20)

    @classmethod
    def intermediate(cls, preview: bool = False) -> "Encoding":
        # scene clips are re-encoded by the join step, so keep them near-lossless
        return cls("ultrafast", 26) if preview else cls("veryfast", 14)

    def args(self) -> list[str]:
        return ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf), "-pix_fmt", "yuv420p"]


def motion_filter(motion: str, w: int, h: int, duration: float) -> str:
    """Filter turning the (possibly oversized) background into a WxH moving frame.

    Zooms use a per-frame ``scale`` + centre ``crop`` (smooth, unlike zoompan's
    integer stepping); pans slide a fixed crop window across a wider image.
    """
    d = max(duration, 0.01)
    if motion == "zoom-in":
        z = f"(1+{ZOOM_AMOUNT}*t/{d:g})"
    elif motion == "zoom-out":
        z = f"(1+{ZOOM_AMOUNT}*(1-t/{d:g}))"
    elif motion in ("pan-left", "pan-right"):
        pw = _even(w * (1 + PAN_EXTRA))
        progress = f"t/{d:g}" if motion == "pan-left" else f"(1-t/{d:g})"
        return f"scale={pw}:-2,crop={w}:{h}:x='(iw-{w})*{progress}':y='(ih-{h})/2'"
    else:
        return f"scale={w}:{h}"
    return f"scale=w='2*trunc({w}*{z}/2)':h=-2:eval=frame,crop={w}:{h}"


def scene_clip_cmd(layers: SceneLayers, out: Path, *, width: int, height: int, fps: int,
                   enc: Encoding) -> list[str]:
    """argv rendering one scene's layers into an MP4 clip of exactly ``duration`` seconds."""
    d = f"{layers.duration:.3f}"
    args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
    inputs = [layers.bg] + ([layers.chrome] if layers.chrome else []) + [l.path for l in layers.fg]
    for p in inputs:
        args += ["-loop", "1", "-framerate", str(fps), "-t", d, "-i", str(p)]

    chains = [f"[0:v]{motion_filter(layers.motion, width, height, layers.duration)},setsar=1,format=rgba[b0]"]
    cur, idx, n = "b0", 1, 0
    if layers.chrome:
        chains.append(f"[{cur}][{idx}:v]overlay=0:0:format=auto[b{idx}]")
        cur, idx = f"b{idx}", idx + 1
    for layer in layers.fg:
        src = f"[{idx}:v]format=rgba"
        if layer.fade > 0:
            src += f",fade=t=in:st={layer.start:.3f}:d={layer.fade:.3f}:alpha=1"
        chains.append(f"{src}[f{n}]")
        chains.append(f"[{cur}][f{n}]overlay=0:0:format=auto[b{idx}]")
        cur, idx, n = f"b{idx}", idx + 1, n + 1
    chains.append(f"[{cur}]format=yuv420p[out]")

    args += ["-filter_complex", ";".join(chains), "-map", "[out]", "-t", d, "-r", str(fps), "-an"]
    args += enc.args() + [str(out)]
    return args


@dataclass(frozen=True)
class JoinSpec:
    path: Path
    duration: float
    transition: str = "none"     # transition INTO this clip
    overlap: float = 0.0         # seconds shared with the previous clip


def join_filter(clips: list[JoinSpec], fps: int) -> tuple[str, float]:
    """filter_complex chaining clips with xfade (or concat for hard cuts).

    Returns the filter and the resulting total duration.
    """
    # same timebase everywhere: concat outputs AVTB, xfade requires both inputs to match
    chains = [f"[{i}:v]fps={fps},settb=AVTB,format=yuv420p[v{i}]" for i in range(len(clips))]
    cur, total = "v0", clips[0].duration
    for i, c in enumerate(clips[1:], start=1):
        out = f"x{i}"
        if c.transition == "none" or c.overlap <= 0:
            chains.append(f"[{cur}][v{i}]concat=n=2:v=1:a=0[{out}]")
            total += c.duration
        else:
            offset = total - c.overlap
            chains.append(f"[{cur}][v{i}]xfade=transition={c.transition}:duration={c.overlap:.3f}:offset={offset:.3f}[{out}]")
            total = offset + c.duration
        cur = out
    chains.append(f"[{cur}]null[out]")
    return ";".join(chains), total


def join_cmd(clips: list[JoinSpec], out: Path, *, fps: int, enc: Encoding) -> list[str]:
    args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
    for c in clips:
        args += ["-i", str(c.path)]
    filt, total = join_filter(clips, fps)
    args += ["-filter_complex", filt, "-map", "[out]", "-t", f"{total:.3f}", "-r", str(fps), "-an"]
    args += enc.args() + ["-movflags", "+faststart", str(out)]
    return args


def thumbnail_cmd(video: Path, out: Path, at: float, width: int = 540) -> list[str]:
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out)]


def run(args: list[str]) -> None:
    res = subprocess.run(args, capture_output=True, text=True)
    if res.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({res.returncode}): {res.stderr.strip()[-2000:]}")


def probe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        raise FFmpegError("ffprobe not found on PATH")
    res = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise FFmpegError(res.stderr.strip())
    return json.loads(res.stdout)


def _even(x: float) -> int:
    return int(round(x / 2) * 2)
