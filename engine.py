"""Moteur FFmpeg : infos vidéo, compression et accélération."""

from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _vendor_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CompressAccelere" / "ffmpeg"
    return _app_dir() / "vendor" / "ffmpeg"


APP_DIR = _app_dir()
VENDOR_DIR = _vendor_dir()
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

ProgressCb = Callable[[float, str], None]
CancelCb = Callable[[], bool]

QUALITY_PRESETS = {
    "Haute": 18,
    "Bonne": 23,
    "Équilibrée": 28,
    "Compacte": 32,
    "Maximale": 36,
}

RESOLUTION_CHOICES = {
    "Conserver": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}


@dataclass
class ClipRange:
    start: float
    end: float


@dataclass
class EditSettings:
    ranges: list[ClipRange] = field(default_factory=list)
    rotate: int = 0
    flip_h: bool = False
    flip_v: bool = False
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    crop: str = "Aucun"
    text: str = ""
    text_pos: str = "Bas"
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    reverse: bool = False
    audio_path: Path | None = None


@dataclass
class VideoInfo:
    path: Path
    duration: float = 0.0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    has_audio: bool = False
    codec: str = ""
    edit: EditSettings = field(default_factory=EditSettings)

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


@dataclass
class ProcessSettings:
    mode: str = "quality"  # "quality" | "size"
    quality_name: str = "Équilibrée"
    target_mb: float = 50.0
    speed: float = 1.0
    resolution: str = "Conserver"
    remove_audio: bool = False
    bitrate_scale: float = 1.0
    output_dir: Path | None = None
    edit: EditSettings = field(default_factory=EditSettings)
    join_all: bool = False


@dataclass
class ProcessResult:
    source: Path
    output: Path | None = None
    ok: bool = False
    error: str = ""
    output_size: int = 0


@dataclass
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path


def format_size(num_bytes: int | float) -> str:
    value = float(num_bytes)
    if value < 1024:
        return f"{int(value)} o"
    if value < 1024**2:
        return f"{value / 1024:.1f} Ko"
    if value < 1024**3:
        return f"{value / (1024**2):.1f} Mo"
    return f"{value / (1024**3):.2f} Go"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def atempo_filter(speed: float) -> str:
    """Chaîne atempo (chaque palier doit rester entre 0.5 et 2.0)."""
    if abs(speed - 1.0) < 0.001:
        return ""
    remaining = max(0.25, min(8.0, speed))
    parts: list[str] = []
    while remaining > 2.0001:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.4999:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts)


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def run_silent(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        startupinfo=_hidden_startupinfo(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def extract_preview_frame(
    tools: FFmpegTools,
    path: Path,
    time_sec: float,
    width: int = 480,
) -> bytes | None:
    cmd = [
        str(tools.ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, time_sec):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "4",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=20,
        startupinfo=_hidden_startupinfo(),
        creationflags=_creationflags(),
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


def extract_frame_to_path(
    tools: FFmpegTools,
    path: Path,
    time_sec: float,
    dest: Path,
) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(tools.ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, time_sec):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(dest),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=30,
        startupinfo=_hidden_startupinfo(),
        creationflags=_creationflags(),
    )
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


CROP_FILTERS = {
    "16:9": r"crop=min(iw\,ih*16/9):min(ih\,iw*9/16)",
    "9:16": r"crop=min(iw\,ih*9/16):min(ih\,iw*16/9)",
    "1:1": r"crop=min(iw\,ih):min(iw\,ih)",
    "4:3": r"crop=min(iw\,ih*4/3):min(ih\,iw*3/4)",
}


def clip_ranges(info: VideoInfo, settings: ProcessSettings | None = None) -> list[ClipRange]:
    edit = settings.edit if settings else info.edit
    duration = max(0.0, info.duration)
    cleaned: list[ClipRange] = []
    for item in edit.ranges:
        start = max(0.0, min(float(item.start), duration if duration else float(item.end)))
        end = max(0.0, min(float(item.end), duration if duration else float(item.end)))
        if end - start >= 0.05:
            cleaned.append(ClipRange(start, end))
    if cleaned:
        return cleaned
    if duration > 0:
        return [ClipRange(0.0, duration)]
    return [ClipRange(0.0, 0.0)]


def clip_source_duration(info: VideoInfo, settings: ProcessSettings) -> float:
    return sum(item.end - item.start for item in clip_ranges(info, settings))


def edited_output_duration(info: VideoInfo, settings: ProcessSettings) -> float:
    source = clip_source_duration(info, settings)
    if source <= 0:
        return 0.0
    return source / max(0.25, min(8.0, settings.speed))


def copy_video(info: VideoInfo) -> VideoInfo:
    return replace(
        info,
        edit=replace(info.edit, ranges=list(info.edit.ranges)),
    )


def _uses_replace_audio(info: VideoInfo, settings: ProcessSettings) -> bool:
    path = settings.edit.audio_path
    return (not settings.remove_audio) and path is not None and path.exists()


def _uses_source_audio(info: VideoInfo, settings: ProcessSettings) -> bool:
    return info.has_audio and not settings.remove_audio and not _uses_replace_audio(info, settings)


def _font_file() -> str | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf", "tahoma.ttf"):
        path = windir / "Fonts" / name
        if path.exists():
            return str(path.resolve()).replace("\\", "/")
    return None


def _drawtext_filter(edit: EditSettings) -> str | None:
    text = (edit.text or "").strip()
    if not text:
        return None
    escaped = (
        text.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
    )
    y = {"Haut": "24", "Centre": "(h-text_h)/2"}.get(edit.text_pos, "h-text_h-24")
    parts = [
        f"text='{escaped}'",
        "fontsize=32",
        "fontcolor=white",
        "borderw=2",
        "bordercolor=black@0.65",
        "x=(w-text_w)/2",
        f"y={y}",
    ]
    font = _font_file()
    if font:
        parts.insert(0, f"fontfile={font.replace(':', r'\:')}")
    return "drawtext=" + ":".join(parts)


def _geometry_filters(edit: EditSettings) -> list[str]:
    filters: list[str] = []
    crop = CROP_FILTERS.get(edit.crop)
    if crop:
        filters.append(crop)
    if edit.rotate == 90:
        filters.append("transpose=1")
    elif edit.rotate == 180:
        filters.append("transpose=1,transpose=1")
    elif edit.rotate == 270:
        filters.append("transpose=2")
    if edit.flip_h:
        filters.append("hflip")
    if edit.flip_v:
        filters.append("vflip")
    return filters


def _color_filters(edit: EditSettings) -> list[str]:
    if (
        abs(edit.brightness) < 0.008
        and abs(edit.contrast - 1.0) < 0.008
        and abs(edit.saturation - 1.0) < 0.008
    ):
        return []
    return [
        "eq="
        f"brightness={edit.brightness:.3f}:"
        f"contrast={edit.contrast:.3f}:"
        f"saturation={edit.saturation:.3f}"
    ]


def _fit_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )


def output_size(info: VideoInfo, settings: ProcessSettings) -> tuple[int, int]:
    width = info.width or 1280
    height = info.height or 720
    crop = settings.edit.crop
    if crop == "1:1":
        side = min(width, height)
        width = height = side
    elif crop == "16:9":
        width, height = int(min(width, height * 16 / 9)), int(min(height, width * 9 / 16))
    elif crop == "9:16":
        width, height = int(min(width, height * 9 / 16)), int(min(height, width * 16 / 9))
    elif crop == "4:3":
        width, height = int(min(width, height * 4 / 3)), int(min(height, width * 3 / 4))
    if settings.edit.rotate in {90, 270}:
        width, height = height, width
    return max(2, width - width % 2), max(2, height - height % 2)


def _segment_video_filters(
    settings: ProcessSettings,
    fit: tuple[int, int] | None = None,
    include_text: bool = False,
) -> list[str]:
    edit = settings.edit
    filters = _geometry_filters(edit)
    filters.extend(_color_filters(edit))
    if edit.reverse:
        filters.append("reverse")
    if fit:
        filters.append(_fit_filter(*fit))
    if abs(settings.speed - 1.0) >= 0.01:
        filters.append(f"setpts=PTS/{settings.speed}")
    if include_text:
        text = _drawtext_filter(edit)
        if text:
            filters.append(text)
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    filters.append("setsar=1")
    return filters


def _final_video_filters(settings: ProcessSettings, out_duration: float) -> list[str]:
    edit = settings.edit
    filters: list[str] = []
    if edit.fade_in > 0.02:
        filters.append(f"fade=t=in:st=0:d={edit.fade_in:.3f}")
    if edit.fade_out > 0.02 and out_duration > edit.fade_out + 0.05:
        start = max(0.0, out_duration - edit.fade_out)
        filters.append(f"fade=t=out:st={start:.3f}:d={edit.fade_out:.3f}")
    text = _drawtext_filter(edit)
    if text:
        filters.append(text)
    return filters


def _audio_filters(settings: ProcessSettings, *, replace: bool) -> list[str]:
    edit = settings.edit
    filters: list[str] = []
    if edit.reverse and not replace:
        filters.append("areverse")
    tempo = atempo_filter(settings.speed)
    if tempo:
        filters.extend(tempo.split(","))
    if abs(edit.volume - 1.0) >= 0.02:
        filters.append(f"volume={max(0.0, edit.volume):.3f}")
    return filters


def _final_audio_filters(settings: ProcessSettings, out_duration: float) -> list[str]:
    edit = settings.edit
    filters: list[str] = []
    if edit.fade_in > 0.02:
        filters.append(f"afade=t=in:st=0:d={edit.fade_in:.3f}")
    if edit.fade_out > 0.02 and out_duration > edit.fade_out + 0.05:
        start = max(0.0, out_duration - edit.fade_out)
        filters.append(f"afade=t=out:st={start:.3f}:d={edit.fade_out:.3f}")
    return filters


def _link(src: str, filters: list[str]) -> str:
    if not filters:
        return src
    if src.endswith("]"):
        return f"{src}{','.join(filters)}"
    return f"{src},{','.join(filters)}"


class PreviewStream:
    """Lit un flux MJPEG FFmpeg en arrière-plan pour l'aperçu."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=2)
        self._running = False

    def start(
        self,
        tools: FFmpegTools,
        path: Path,
        start_sec: float,
        width: int = 480,
        speed: float = 1.0,
    ) -> None:
        self.stop()
        vf = f"scale={width}:-2"
        if abs(speed - 1.0) >= 0.01:
            vf = f"{vf},setpts=PTS/{max(0.25, min(8.0, speed))}"
        cmd = [
            str(tools.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start_sec):.3f}",
            "-i",
            str(path),
            "-an",
            "-vf",
            vf,
            "-q:v",
            "5",
            "-f",
            "mjpeg",
            "pipe:1",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            startupinfo=_hidden_startupinfo(),
            creationflags=_creationflags(),
        )
        self._running = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        buf = b""
        while self._running and proc.poll() is None:
            chunk = proc.stdout.read(8192)
            if not chunk:
                break
            buf += chunk
            while True:
                start = buf.find(b"\xff\xd8")
                if start < 0:
                    buf = buf[-1:] if buf else b""
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end < 0:
                    buf = buf[start:]
                    break
                frame = buf[start : end + 2]
                buf = buf[end + 2 :]
                if self.frames.full():
                    try:
                        self.frames.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    pass
        self._running = False

    def take_frame(self) -> bytes | None:
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._running = False
        proc = self.proc
        self.proc = None
        if proc and proc.poll() is None:
            _stop_process(proc)
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break


def _which_tools() -> FFmpegTools | None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return FFmpegTools(Path(ffmpeg), Path(ffprobe))

    local_ff = VENDOR_DIR / "ffmpeg.exe"
    local_fp = VENDOR_DIR / "ffprobe.exe"
    if local_ff.exists() and local_fp.exists():
        return FFmpegTools(local_ff, local_fp)
    return None


def ensure_ffmpeg(progress: ProgressCb | None = None) -> FFmpegTools:
    existing = _which_tools()
    if existing:
        return existing

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = VENDOR_DIR / "ffmpeg.zip"

    def report(pct: float, message: str) -> None:
        if progress:
            progress(pct, message)

    report(0.02, "Téléchargement de FFmpeg…")

    def hook(block_num: int, block_size: int, total: int) -> None:
        if total > 0:
            pct = min(0.75, 0.05 + 0.70 * (block_num * block_size / total))
            report(pct, "Téléchargement de FFmpeg…")

    try:
        urllib.request.urlretrieve(FFMPEG_URL, zip_path, hook)
    except Exception as exc:
        raise RuntimeError(
            "Impossible de télécharger FFmpeg. Vérifiez Internet, puis relancez."
        ) from exc

    report(0.78, "Extraction de FFmpeg…")
    extract_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_extract_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extract_dir)

        found_ffmpeg = next(extract_dir.rglob("ffmpeg.exe"), None)
        found_ffprobe = next(extract_dir.rglob("ffprobe.exe"), None)
        if not found_ffmpeg or not found_ffprobe:
            raise RuntimeError("L'archive FFmpeg ne contient pas ffmpeg.exe / ffprobe.exe.")

        shutil.copy2(found_ffmpeg, VENDOR_DIR / "ffmpeg.exe")
        shutil.copy2(found_ffprobe, VENDOR_DIR / "ffprobe.exe")
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)

    report(1.0, "FFmpeg est prêt.")
    tools = _which_tools()
    if not tools:
        raise RuntimeError("FFmpeg a été installé mais reste introuvable.")
    return tools


def _parse_rate(rate: object) -> float:
    text = str(rate or "")
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            denom = float(den)
            return float(num) / denom if denom else 0.0
        return float(text)
    except ValueError:
        return 0.0


def _probe_duration(fmt: dict, video_stream: dict) -> float:
    values: list[float] = []
    fps = _parse_rate(video_stream.get("avg_frame_rate")) or _parse_rate(
        video_stream.get("r_frame_rate")
    )
    nb = video_stream.get("nb_frames")
    try:
        if nb and str(nb).isdigit() and int(nb) > 0 and fps > 0:
            values.append(int(nb) / fps)
    except (TypeError, ValueError):
        pass
    for raw in (video_stream.get("duration"), fmt.get("duration")):
        try:
            if raw not in (None, "N/A", "", "0"):
                val = float(raw)
                if val > 0.2:
                    values.append(val)
        except (TypeError, ValueError):
            pass
    if not values:
        return 0.0
    values.sort()
    if len(values) >= 2 and values[-1] > values[0] * 1.2:
        return values[0]
    return values[0]


def probe_video(tools: FFmpegTools, path: Path) -> VideoInfo:
    args = [
        str(tools.ffprobe),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = run_silent(args, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"Impossible de lire la vidéo : {path.name}")

    data = json.loads(proc.stdout or "{}")
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )
    fmt = data.get("format", {})
    duration = _probe_duration(fmt, video_stream)
    size_bytes = int(fmt.get("size") or path.stat().st_size)
    return VideoInfo(
        path=path,
        duration=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        size_bytes=size_bytes,
        has_audio=audio_stream is not None,
        codec=str(video_stream.get("codec_name") or ""),
    )


def output_duration(info: VideoInfo, speed: float) -> float:
    speed = max(0.25, min(8.0, speed))
    if info.duration <= 0:
        return 0.0
    return info.duration / speed


def estimate_output_size(info: VideoInfo, settings: ProcessSettings) -> int:
    if settings.mode == "size":
        return int(max(1.0, settings.target_mb) * 1024 * 1024)

    crf = QUALITY_PRESETS.get(settings.quality_name, 28)
    # Approximation empirique : un CRF plus élevé réduit fortement le poids.
    factor = max(0.06, min(0.95, 1.35 - crf * 0.035))
    height = RESOLUTION_CHOICES.get(settings.resolution)
    if height and info.height:
        factor *= min(1.0, (height / info.height) ** 2)
    factor /= max(0.25, settings.speed)
    return max(int(info.size_bytes * factor), 256 * 1024)


def suggested_output_name(info: VideoInfo, settings: ProcessSettings) -> str:
    parts = [info.path.stem]
    if settings.join_all:
        parts = [info.path.stem, "fusion"]
    if abs(settings.speed - 1.0) >= 0.01:
        speed_label = (
            f"{settings.speed:.2f}".rstrip("0").rstrip(".").replace(".", "p")
        )
        parts.append(f"{speed_label}x")
    ranges = clip_ranges(info, settings)
    full = info.duration <= 0 or (
        len(ranges) == 1 and ranges[0].start <= 0.04 and ranges[0].end >= info.duration - 0.04
    )
    if not full:
        parts.append("coupe")
    if settings.edit.rotate:
        parts.append(f"rot{settings.edit.rotate}")
    if settings.mode == "size":
        parts.append(f"{int(round(settings.target_mb))}mo")
    if settings.remove_audio:
        parts.append("muet")
    return "_".join(parts) + ".mp4"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _target_bitrates(info: VideoInfo, settings: ProcessSettings) -> tuple[int, int]:
    """Débits vidéo/audio (kb/s) pour viser la taille demandée."""
    duration = edited_output_duration(info, settings) or output_duration(info, settings.speed)
    if duration <= 0:
        duration = 1.0
    target_bits = max(1.0, settings.target_mb) * 1024 * 1024 * 8
    total_kbps = max(64, int(target_bits / duration / 1000))
    if not info.has_audio or settings.remove_audio:
        video_kbps = max(48, int(total_kbps * settings.bitrate_scale))
        return video_kbps, 0
    audio_kbps = int(total_kbps * 0.10)
    audio_kbps = max(32, min(96, audio_kbps, int(total_kbps * 0.22)))
    video_kbps = max(48, int((total_kbps - audio_kbps) * settings.bitrate_scale))
    return video_kbps, audio_kbps


def _audio_kbps(info: VideoInfo, settings: ProcessSettings) -> int:
    if settings.remove_audio:
        return 0
    if not info.has_audio and not _uses_replace_audio(info, settings):
        return 0
    duration = edited_output_duration(info, settings) or output_duration(info, settings.speed) or 1.0
    total_kbps = max(64, int(settings.target_mb * 1024 * 8 / duration))
    return max(48, min(96, int(total_kbps * 0.08)))


def _estimate_crf(probe_crf: int, probe_bytes: int, target_bytes: float) -> int:
    """Un palier CRF de 6 ≈ x2 / ÷2 sur le poids. On vise la taille sans gâcher la qualité."""
    if probe_bytes <= 0:
        return 23
    ratio = max(0.12, min(10.0, target_bytes / probe_bytes))
    estimated = probe_crf - 6.0 * math.log2(ratio)
    return int(round(max(12, min(32, estimated))))


def _ffmpeg_base(tools: FFmpegTools) -> list[str]:
    return [
        str(tools.ffmpeg),
        "-y",
        "-hide_banner",
        "-progress",
        "pipe:1",
        "-nostats",
        "-loglevel",
        "error",
    ]


def _video_encode_args(
    info: VideoInfo,
    settings: ProcessSettings,
    crf: int | None,
    preset: str,
) -> tuple[list[str], int]:
    args = [
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-x264-params",
        "aq-mode=3",
    ]
    audio_k = _audio_kbps(info, settings)
    if crf is not None:
        args.extend(["-crf", str(crf)])
    elif settings.mode == "size":
        video_k, audio_from_size = _target_bitrates(info, settings)
        audio_k = audio_from_size
        args.extend(["-b:v", f"{video_k}k"])
    else:
        args.extend(["-crf", str(QUALITY_PRESETS.get(settings.quality_name, 28))])
    return args, audio_k


def _needs_filter_graph(info: VideoInfo, settings: ProcessSettings) -> bool:
    return len(clip_ranges(info, settings)) > 1 or _uses_replace_audio(info, settings)


def _build_simple_command(
    tools: FFmpegTools,
    info: VideoInfo,
    settings: ProcessSettings,
    output: Path,
    *,
    crf: int | None,
    preset: str,
) -> list[str]:
    rng = clip_ranges(info, settings)[0]
    out_dur = edited_output_duration(info, settings) or 1.0
    cmd = _ffmpeg_base(tools)
    trimmed = info.duration > 0 and (
        rng.start > 0.03 or rng.end < info.duration - 0.03
    )
    if trimmed and rng.start > 0.02:
        cmd.extend(["-ss", f"{rng.start:.3f}"])
    if trimmed:
        cmd.extend(["-t", f"{max(0.05, rng.end - rng.start):.3f}"])
    cmd.extend(["-i", str(info.path)])

    if (
        settings.edit.rotate
        or settings.edit.flip_h
        or settings.edit.flip_v
        or settings.edit.reverse
        or settings.edit.crop != "Aucun"
        or settings.edit.text.strip()
        or abs(settings.edit.brightness) >= 0.008
        or abs(settings.edit.contrast - 1.0) >= 0.008
        or abs(settings.edit.saturation - 1.0) >= 0.008
        or settings.edit.fade_in > 0.02
        or settings.edit.fade_out > 0.02
        or abs(settings.speed - 1.0) >= 0.01
    ):
        vf = _segment_video_filters(settings, include_text=False) + _final_video_filters(
            settings, out_dur
        )
        if vf:
            cmd.extend(["-vf", ",".join(vf)])

    encode_args, audio_k = _video_encode_args(info, settings, crf, preset)
    cmd.extend(encode_args)

    if _uses_source_audio(info, settings):
        af = _audio_filters(settings, replace=False) + _final_audio_filters(
            settings, out_dur
        )
        if af:
            cmd.extend(["-af", ",".join(af)])
        cmd.extend(["-c:a", "aac", "-b:a", f"{audio_k}k"])
    else:
        cmd.append("-an")

    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


def _build_filter_command(
    tools: FFmpegTools,
    info: VideoInfo,
    settings: ProcessSettings,
    output: Path,
    *,
    crf: int | None,
    preset: str,
) -> list[str]:
    ranges = clip_ranges(info, settings)
    out_dur = edited_output_duration(info, settings) or 1.0
    replace = _uses_replace_audio(info, settings)
    cmd = _ffmpeg_base(tools)
    cmd.extend(["-i", str(info.path)])
    if replace:
        cmd.extend(["-i", str(settings.edit.audio_path)])

    graph: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    source_audio = _uses_source_audio(info, settings)
    want_audio = replace or source_audio
    for index, rng in enumerate(ranges):
        v_in = _link(
            f"[0:v]trim={rng.start:.3f}:{rng.end:.3f},setpts=PTS-STARTPTS",
            _segment_video_filters(settings, include_text=False),
        )
        graph.append(f"{v_in}[v{index}]")
        v_labels.append(f"[v{index}]")
        if not source_audio:
            continue
        a_in = _link(
            f"[0:a]atrim={rng.start:.3f}:{rng.end:.3f},asetpts=PTS-STARTPTS",
            _audio_filters(settings, replace=False),
        )
        graph.append(f"{a_in}[a{index}]")
        a_labels.append(f"[a{index}]")

    if len(v_labels) == 1:
        video_src = v_labels[0]
        audio_src = a_labels[0] if a_labels else ""
    elif want_audio and a_labels:
        graph.append(
            f"{''.join(v + a for v, a in zip(v_labels, a_labels))}"
            f"concat=n={len(v_labels)}:v=1:a=1[vcat][acat]"
        )
        video_src = "[vcat]"
        audio_src = "[acat]"
    else:
        graph.append(f"{''.join(v_labels)}concat=n={len(v_labels)}:v=1:a=0[vcat]")
        video_src = "[vcat]"
        audio_src = ""

    if replace:
        a_rep = _link(
            f"[1:a]atrim=0:{out_dur:.3f},asetpts=PTS-STARTPTS",
            _audio_filters(settings, replace=True),
        )
        graph.append(f"{a_rep}[arep]")
        audio_src = "[arep]"

    final = _final_video_filters(settings, out_dur)
    if final:
        graph.append(f"{_link(video_src, final)}[vout]")
        video_src = "[vout]"
    final_a = _final_audio_filters(settings, out_dur)
    if audio_src and final_a:
        graph.append(f"{_link(audio_src, final_a)}[aout]")
        audio_src = "[aout]"

    cmd.extend(["-filter_complex", ";".join(graph)])
    cmd.extend(["-map", video_src])
    encode_args, audio_k = _video_encode_args(info, settings, crf, preset)
    cmd.extend(encode_args)
    if want_audio and audio_src:
        cmd.extend(["-map", audio_src, "-c:a", "aac", "-b:a", f"{audio_k}k"])
    else:
        cmd.append("-an")
    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


def _build_command(
    tools: FFmpegTools,
    info: VideoInfo,
    settings: ProcessSettings,
    output: Path | None = None,
    *,
    crf: int | None = None,
    preset: str = "medium",
) -> list[str]:
    if output is None:
        raise ValueError("Le fichier de sortie est obligatoire.")
    if _needs_filter_graph(info, settings):
        return _build_filter_command(
            tools, info, settings, output, crf=crf, preset=preset
        )
    return _build_simple_command(tools, info, settings, output, crf=crf, preset=preset)


def _build_join_command(
    tools: FFmpegTools,
    items: list[tuple[VideoInfo, ProcessSettings]],
    base: ProcessSettings,
    joined: VideoInfo,
    output: Path,
    *,
    crf: int | None,
    preset: str,
) -> list[str]:
    cmd = _ffmpeg_base(tools)
    inputs: list[Path] = []
    segments: list[tuple[int, int | None, VideoInfo, ProcessSettings, ClipRange]] = []
    for info, settings in items:
        video_idx = len(inputs)
        inputs.append(info.path)
        audio_idx = None
        if _uses_replace_audio(info, settings):
            audio_idx = len(inputs)
            inputs.append(settings.edit.audio_path)  # type: ignore[arg-type]
        for rng in clip_ranges(info, settings):
            segments.append((video_idx, audio_idx, info, settings, rng))

    for path in inputs:
        cmd.extend(["-i", str(path)])

    fit = output_size(items[0][0], items[0][1])
    out_dur = edited_output_duration(joined, base) or 1.0
    want_audio = (not base.remove_audio) and any(
        _uses_source_audio(info, settings) or _uses_replace_audio(info, settings)
        for info, settings in items
    )

    graph: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    for index, (video_idx, audio_idx, info, settings, rng) in enumerate(segments):
        seg_out = max(0.05, (rng.end - rng.start) / max(0.25, min(8.0, settings.speed)))
        v_in = _link(
            f"[{video_idx}:v]trim={rng.start:.3f}:{rng.end:.3f},setpts=PTS-STARTPTS",
            _segment_video_filters(settings, fit=fit, include_text=True),
        )
        graph.append(f"{v_in}[v{index}]")
        v_labels.append(f"[v{index}]")
        if not want_audio:
            continue
        if audio_idx is not None:
            a_in = _link(
                f"[{audio_idx}:a]atrim=0:{seg_out:.3f},asetpts=PTS-STARTPTS",
                _audio_filters(settings, replace=True),
            )
        elif _uses_source_audio(info, settings):
            a_in = _link(
                f"[{video_idx}:a]atrim={rng.start:.3f}:{rng.end:.3f},asetpts=PTS-STARTPTS",
                _audio_filters(settings, replace=False),
            )
        else:
            a_in = (
                f"anullsrc=channel_layout=stereo:sample_rate=44100,"
                f"atrim=0:{seg_out:.3f},asetpts=PTS-STARTPTS"
            )
        graph.append(f"{a_in}[a{index}]")
        a_labels.append(f"[a{index}]")

    if want_audio:
        graph.append(
            f"{''.join(v + a for v, a in zip(v_labels, a_labels))}"
            f"concat=n={len(v_labels)}:v=1:a=1[vcat][acat]"
        )
        video_src = "[vcat]"
        audio_src = "[acat]"
    else:
        graph.append(f"{''.join(v_labels)}concat=n={len(v_labels)}:v=1:a=0[vcat]")
        video_src = "[vcat]"
        audio_src = ""

    fade_only = replace(base, edit=replace(base.edit, text=""))
    final = _final_video_filters(fade_only, out_dur)
    if final:
        graph.append(f"{_link(video_src, final)}[vout]")
        video_src = "[vout]"
    final_a = _final_audio_filters(base, out_dur)
    if audio_src and final_a:
        graph.append(f"{_link(audio_src, final_a)}[aout]")
        audio_src = "[aout]"

    cmd.extend(["-filter_complex", ";".join(graph)])
    cmd.extend(["-map", video_src])
    encode_args, audio_k = _video_encode_args(joined, base, crf, preset)
    cmd.extend(encode_args)
    if want_audio and audio_src:
        cmd.extend(["-map", audio_src, "-c:a", "aac", "-b:a", f"{audio_k}k"])
    else:
        cmd.append("-an")
    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


def _parse_progress(line: str) -> float | None:
    if "N/A" in line:
        return None
    match = re.search(r"out_time_us=(\d+)", line)
    if match:
        return int(match.group(1)) / 1_000_000
    match = re.search(r"out_time_ms=(\d+)", line)
    if match:
        return int(match.group(1)) / 1_000
    match = re.search(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
    if match:
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return None


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            startupinfo=_hidden_startupinfo(),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _run_ffmpeg(
    cmd: list[str],
    expected: float,
    progress: ProgressCb | None,
    should_cancel: CancelCb | None,
    progress_start: float,
    progress_span: float,
    status: str,
) -> tuple[int, str, bool]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        startupinfo=_hidden_startupinfo(),
        creationflags=_creationflags(),
    )

    stdout_q: queue.Queue[bytes | None] = queue.Queue()
    stderr_chunks: list[bytes] = []

    def read_stdout() -> None:
        try:
            if proc.stdout:
                for line in proc.stdout:
                    stdout_q.put(line)
        finally:
            stdout_q.put(None)

    def read_stderr() -> None:
        if not proc.stderr:
            return
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            stderr_chunks.append(chunk)
            if sum(len(part) for part in stderr_chunks) > 80_000:
                del stderr_chunks[:-6]

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    last_ui = 0.0
    try:
        while True:
            if should_cancel and should_cancel():
                _stop_process(proc)
                return 0, "Annulé", True

            try:
                raw = stdout_q.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    while True:
                        try:
                            leftover = stdout_q.get_nowait()
                        except queue.Empty:
                            break
                        if leftover is None:
                            break
                    break
                continue

            if raw is None:
                break

            line = raw.decode("utf-8", errors="replace").strip()
            seconds = _parse_progress(line)
            now = time.monotonic()
            if seconds is not None and progress and now - last_ui >= 0.2:
                last_ui = now
                local = max(0.0, min(1.0, seconds / max(expected, 0.1)))
                progress(progress_start + local * progress_span, status)
            if line.startswith("progress=end") and progress:
                progress(progress_start + progress_span, status)

        code = proc.wait(timeout=60)
        error = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()[-800:]
        return code, error, False
    except subprocess.TimeoutExpired:
        _stop_process(proc)
        return 1, "FFmpeg ne répond plus à la fin de l'encodage.", False
    except Exception as exc:
        _stop_process(proc)
        return 1, str(exc), False


def process_video(
    tools: FFmpegTools,
    info: VideoInfo,
    settings: ProcessSettings,
    progress: ProgressCb | None = None,
    should_cancel: CancelCb | None = None,
) -> ProcessResult:
    dest_dir = settings.output_dir or info.path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    output = unique_path(dest_dir / suggested_output_name(info, settings))
    expected = edited_output_duration(info, settings) or info.duration or 1.0
    probe_path = dest_dir / f".probe_{output.stem}.mp4"

    def run(
        dest: Path,
        crf: int | None,
        preset: str,
        start: float,
        span: float,
        label: str,
    ) -> tuple[int, str, bool]:
        cmd = _build_command(
            tools, info, settings, output=dest, crf=crf, preset=preset
        )
        return _run_ffmpeg(cmd, expected, progress, should_cancel, start, span, label)

    try:
        if settings.mode == "size":
            target_bytes = max(1.0, settings.target_mb) * 1024 * 1024
            probe_crf = 22
            code, error, cancelled = run(
                probe_path,
                probe_crf,
                "veryfast",
                0.0,
                0.34,
                f"Analyse qualité de {info.path.name}…",
            )
            if cancelled:
                probe_path.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
                return ProcessResult(source=info.path, error="Annulé")
            if code != 0 or not probe_path.exists():
                probe_path.unlink(missing_ok=True)
                return ProcessResult(
                    source=info.path,
                    error=error or "L'analyse de qualité a échoué.",
                )

            probe_size = probe_path.stat().st_size
            crf = _estimate_crf(probe_crf, probe_size, target_bytes)
            probe_path.unlink(missing_ok=True)

            code, error, cancelled = run(
                output,
                crf,
                "medium",
                0.34,
                0.50,
                f"Encodage de {info.path.name}…",
            )
            if cancelled:
                output.unlink(missing_ok=True)
                return ProcessResult(source=info.path, error="Annulé")
            if code != 0 or not output.exists():
                output.unlink(missing_ok=True)
                return ProcessResult(
                    source=info.path,
                    error=error or f"FFmpeg a échoué (code {code}).",
                )

            actual = output.stat().st_size
            if actual > target_bytes * 1.12 and crf < 32:
                output.unlink(missing_ok=True)
                crf = min(32, crf + 2)
                code, error, cancelled = run(
                    output,
                    crf,
                    "medium",
                    0.84,
                    0.14,
                    "Ajustement : un peu plus compact…",
                )
            elif actual < target_bytes * 0.88 and crf > 12:
                output.unlink(missing_ok=True)
                crf = max(12, crf - 2)
                code, error, cancelled = run(
                    output,
                    crf,
                    "medium",
                    0.84,
                    0.14,
                    "Ajustement : meilleure qualité…",
                )
            if cancelled:
                output.unlink(missing_ok=True)
                return ProcessResult(source=info.path, error="Annulé")
            if code != 0 or not output.exists():
                output.unlink(missing_ok=True)
                return ProcessResult(
                    source=info.path,
                    error=error or "L'ajustement qualité/taille a échoué.",
                )
        else:
            code, error, cancelled = run(
                output, None, "medium", 0.0, 0.99, f"Encodage de {info.path.name}…"
            )
            if cancelled:
                output.unlink(missing_ok=True)
                return ProcessResult(source=info.path, error="Annulé")
            if code != 0 or not output.exists():
                output.unlink(missing_ok=True)
                return ProcessResult(
                    source=info.path,
                    error=error or f"FFmpeg a échoué (code {code}).",
                )

        if progress:
            progress(1.0, "Terminé.")
        return ProcessResult(
            source=info.path,
            output=output,
            ok=True,
            output_size=output.stat().st_size,
        )
    except Exception as exc:
        probe_path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        return ProcessResult(source=info.path, error=str(exc))


def process_joined(
    tools: FFmpegTools,
    items: list[tuple[VideoInfo, ProcessSettings]],
    base: ProcessSettings,
    progress: ProgressCb | None = None,
    should_cancel: CancelCb | None = None,
) -> ProcessResult:
    if not items:
        return ProcessResult(source=Path("fusion"), error="Aucune vidéo à joindre.")
    first, _ = items[0]
    width, height = output_size(first, items[0][1])
    source_dur = sum(clip_source_duration(info, settings) for info, settings in items)
    joined = VideoInfo(
        path=first.path.with_name(first.path.stem + "_fusion" + first.path.suffix),
        duration=source_dur,
        width=width,
        height=height,
        size_bytes=sum(info.size_bytes for info, _ in items),
        has_audio=any(
            _uses_source_audio(info, settings) or _uses_replace_audio(info, settings)
            for info, settings in items
        ),
        edit=base.edit,
    )
    join_settings = replace(base, join_all=True, edit=base.edit)
    dest_dir = join_settings.output_dir or first.path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    output = unique_path(dest_dir / suggested_output_name(joined, join_settings))
    expected = edited_output_duration(joined, join_settings) or 1.0
    probe_path = dest_dir / f".probe_{output.stem}.mp4"

    def run(
        dest: Path,
        crf: int | None,
        preset: str,
        start: float,
        span: float,
        label: str,
    ) -> tuple[int, str, bool]:
        cmd = _build_join_command(
            tools, items, join_settings, joined, dest, crf=crf, preset=preset
        )
        return _run_ffmpeg(cmd, expected, progress, should_cancel, start, span, label)

    try:
        target_bytes = max(1.0, join_settings.target_mb) * 1024 * 1024
        probe_crf = 22
        code, error, cancelled = run(
            probe_path, probe_crf, "veryfast", 0.0, 0.34, "Analyse de la fusion…"
        )
        if cancelled:
            probe_path.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            return ProcessResult(source=joined.path, error="Annulé")
        if code != 0 or not probe_path.exists():
            probe_path.unlink(missing_ok=True)
            return ProcessResult(
                source=joined.path,
                error=error or "L'analyse de la fusion a échoué.",
            )
        crf = _estimate_crf(probe_crf, probe_path.stat().st_size, target_bytes)
        probe_path.unlink(missing_ok=True)
        code, error, cancelled = run(
            output, crf, "medium", 0.34, 0.50, "Encodage de la fusion…"
        )
        if cancelled:
            output.unlink(missing_ok=True)
            return ProcessResult(source=joined.path, error="Annulé")
        if code != 0 or not output.exists():
            output.unlink(missing_ok=True)
            return ProcessResult(
                source=joined.path,
                error=error or "La fusion a échoué.",
            )
        actual = output.stat().st_size
        if actual > target_bytes * 1.12 and crf < 32:
            output.unlink(missing_ok=True)
            crf = min(32, crf + 2)
            code, error, cancelled = run(
                output, crf, "medium", 0.84, 0.14, "Ajustement : un peu plus compact…"
            )
        elif actual < target_bytes * 0.88 and crf > 12:
            output.unlink(missing_ok=True)
            crf = max(12, crf - 2)
            code, error, cancelled = run(
                output, crf, "medium", 0.84, 0.14, "Ajustement : meilleure qualité…"
            )
        if cancelled:
            output.unlink(missing_ok=True)
            return ProcessResult(source=joined.path, error="Annulé")
        if code != 0 or not output.exists():
            output.unlink(missing_ok=True)
            return ProcessResult(
                source=joined.path,
                error=error or "L'ajustement de la fusion a échoué.",
            )
        if progress:
            progress(1.0, "Terminé.")
        return ProcessResult(
            source=joined.path,
            output=output,
            ok=True,
            output_size=output.stat().st_size,
        )
    except Exception as exc:
        probe_path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        return ProcessResult(source=joined.path, error=str(exc))


def default_output_dir() -> Path:
    videos = Path.home() / "Videos"
    if videos.exists():
        return videos
    return Path.home()
