from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
DEFAULT_DOWNLOADS = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"
HISTORY_FILE = DATA_DIR / "history.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOADS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "app.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("clipforge")

# yt-dlp is loaded lazily so the GUI/backend can appear without waiting for the
# heavy extractor module. This materially reduces cold-start time.
_yt_dlp = None
_ffmpeg_ready = False
_ffmpeg_repair_lock = threading.Lock()

FFMPEG_ARCHIVE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

def _ffmpeg_command_works(exe_name: str) -> bool:
    try:
        exe = shutil.which(exe_name)
        if not exe:
            return False
        result = subprocess.run(
            [exe, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False

def _install_ffmpeg_from_zip(local_bin: Path) -> bool:
    """Download and install the complete FFmpeg bin folder, including DLLs."""
    local_bin.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="ClipForge-ffmpeg-"))
    archive = tmp_root / "ffmpeg.zip"
    extract = tmp_root / "extract"
    try:
        req = urllib.request.Request(
            FFMPEG_ARCHIVE_URL,
            headers={"User-Agent": "ClipForge/2.4.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract)

        candidates = []
        for ff in extract.rglob("ffmpeg.exe"):
            bin_dir = ff.parent
            if (bin_dir / "ffprobe.exe").exists():
                candidates.append(bin_dir)
        if not candidates:
            raise FileNotFoundError("FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe")

        source_bin = candidates[0]
        # Remove stale/broken FFmpeg files before copying a complete matching set.
        for child in local_bin.iterdir():
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
            except OSError:
                pass
        for child in source_bin.iterdir():
            target = local_bin / child.name
            if child.is_file():
                shutil.copy2(child, target)
        return (local_bin / "ffmpeg.exe").exists() and (local_bin / "ffprobe.exe").exists()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

def ensure_ffmpeg() -> bool:
    """Ensure a working, self-contained FFmpeg + FFprobe runtime is available.

    The installer used to copy only ffmpeg.exe from some Windows builds. Shared
    builds then failed at runtime with errors such as `avformat-63.dll was not
    found`. We now validate the executable, and automatically replace an
    incomplete/broken runtime with the complete Gyan Essentials bin folder.
    """
    global _ffmpeg_ready
    if _ffmpeg_ready and _ffmpeg_command_works("ffmpeg") and _ffmpeg_command_works("ffprobe"):
        return True

    local_bin = ROOT / "runtime" / "ffmpeg" / "bin"
    with _ffmpeg_repair_lock:
        # Prefer the app-local runtime over any unrelated system installation.
        os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")
        if _ffmpeg_command_works("ffmpeg") and _ffmpeg_command_works("ffprobe"):
            _ffmpeg_ready = True
            return True

        update_job_hint = getattr(log, "info", None)
        if update_job_hint:
            log.info("FFmpeg runtime missing/broken. Starting automatic repair.")

        try:
            ok = _install_ffmpeg_from_zip(local_bin)
            os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")
            _ffmpeg_ready = ok and _ffmpeg_command_works("ffmpeg") and _ffmpeg_command_works("ffprobe")
            if _ffmpeg_ready:
                log.info("FFmpeg automatic repair completed successfully.")
                return True
        except Exception:
            log.exception("Direct FFmpeg automatic repair failed")

        # Last-resort fallback. Keep imageio-ffmpeg only as a fallback, not the
        # primary Windows runtime, because the app needs a complete ffprobe pair.
        try:
            import imageio_ffmpeg
            exe = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
            if exe.exists():
                os.environ["PATH"] = str(exe.parent) + os.pathsep + os.environ.get("PATH", "")
                _ffmpeg_ready = _ffmpeg_command_works("ffmpeg")
                if _ffmpeg_ready:
                    log.info("FFmpeg initialized using imageio-ffmpeg fallback: %s", exe)
                    return True
        except Exception:
            log.exception("Could not initialize FFmpeg fallback runtime")

    return False

def get_yt_dlp():
    global _yt_dlp
    if _yt_dlp is None:
        try:
            import yt_dlp as _module
            _yt_dlp = _module
        except Exception:
            log.exception("yt-dlp import failed")
            raise
    return _yt_dlp

app = Flask(__name__, static_folder=None)

jobs: dict[str, dict] = {}
controls: dict[str, dict[str, threading.Event]] = {}
jobs_lock = threading.RLock()


def valid_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def safe_path(value: str | None) -> Path:
    raw = (value or "").strip()
    p = Path(raw).expanduser() if raw else DEFAULT_DOWNLOADS
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p.resolve()


def resolve_downloaded_file(prepared: Path, dest: Path, mode: str, requested_ext: str) -> Path:
    """Resolve the exact final file after yt-dlp/FFmpeg post-processing."""
    requested_ext = requested_ext.lower().lstrip(".")
    candidates = []

    # The normal yt-dlp output path.
    expected = prepared.with_suffix("." + requested_ext)
    if expected.is_file():
        return expected.resolve()

    # Some post-processors change the extension or append a conversion suffix.
    stem = prepared.stem
    for p in dest.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower().lstrip(".") != requested_ext:
            continue
        if p.stem == stem or p.stem.startswith(stem + ".") or p.stem.startswith(stem + "-"):
            candidates.append(p)

    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime).resolve()

    # Final fallback: the newest requested-format media file in the destination.
    if mode == "audio":
        allowed = {"mp3", "m4a", "opus", "wav"}
    else:
        allowed = {"mp4", "mkv", "webm"}
    candidates = [p for p in dest.iterdir() if p.is_file() and p.suffix.lower().lstrip(".") == requested_ext and requested_ext in allowed]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime).resolve()

    raise FileNotFoundError(f"Download completed, but the final .{requested_ext} file could not be located.")


def open_local_path(path: Path):
    """Open the exact local file or exact folder using Windows shell semantics."""
    path = Path(path).expanduser()
    try:
        path = path.resolve(strict=True)
    except FileNotFoundError:
        raise FileNotFoundError(str(path))

    if os.name == "nt":
        import ctypes
        target = str(path)
        # ShellExecuteW with the explicit ``open`` verb is the closest native
        # equivalent to double-clicking the exact item in Windows Explorer.
        # It preserves spaces, Unicode and the user's registered file handler.
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "open", target, None, str(path.parent), 1
        )
        if result <= 32:
            # os.startfile is a second native Windows fallback.
            try:
                os.startfile(target)
                return
            except OSError:
                raise OSError(f"Windows could not open '{target}' (ShellExecute code {result}).")
        return

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen(
        [opener, str(path)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def safe_filename(value: str, fallback: str = "Download") -> str:
    """Return a Windows-safe user supplied filename without an extension."""
    value = str(value or "").strip()
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value)
    value = re.sub(r"[. ]+$", "", value).strip()
    if not value:
        value = fallback
    # Windows reserves these device names even with a normal-looking suffix.
    if value.upper().split(".", 1)[0] in {"CON", "PRN", "AUX", "NUL",
                                            "COM1", "COM2", "COM3", "COM4",
                                            "COM5", "COM6", "COM7", "COM8", "COM9",
                                            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
                                            "LPT6", "LPT7", "LPT8", "LPT9"}:
        value = "_" + value
    return value[:180].rstrip(" .") or fallback


def fmt_bytes(n):
    if n in (None, 0):
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def load_history():
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        log.exception("Could not load history")
    return []


def save_history():
    with jobs_lock:
        completed = [j for j in jobs.values() if j.get("status") == "finished"]
        completed = sorted(completed, key=lambda x: x.get("created_at", 0), reverse=True)[:200]
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_FILE)


def source_name(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host or "Unknown source"


def analyze(url: str):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }
    yt_dlp = get_yt_dlp()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = []
    resolutions = set()
    fps_values = set()
    extensions = set()
    for f in info.get("formats", []):
        if not f.get("format_id"):
            continue
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        has_video = vcodec not in (None, "none")
        has_audio = acodec not in (None, "none")
        if not (has_video or has_audio):
            continue
        h = f.get("height")
        fps = f.get("fps")
        ext = f.get("ext") or ""
        if has_video and h:
            resolutions.add(int(h))
        if has_video and fps:
            fps_values.add(float(fps))
        if ext:
            extensions.add(ext)
        formats.append(
            {
                "id": str(f["format_id"]),
                "ext": ext,
                "height": h,
                "width": f.get("width"),
                "fps": fps,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "vcodec": vcodec,
                "acodec": acodec,
                "tbr": f.get("tbr"),
            }
        )

    return {
        "id": info.get("id"),
        "title": info.get("title") or "Untitled media",
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "source": source_name(url),
        "formats": formats,
        "resolutions": sorted(resolutions, reverse=True),
        "fps": sorted(fps_values),
        "extensions": sorted(extensions),
    }


def update_job(job_id, **values):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)


def progress_hook(job_id):
    def hook(d):
        ctl = controls.get(job_id, {})
        cancel_event = ctl.get("cancel")
        pause_event = ctl.get("pause")
        if cancel_event and cancel_event.is_set():
            raise get_yt_dlp().utils.DownloadCancelled("Cancelled by user")
        while pause_event and pause_event.is_set():
            update_job(job_id, status="paused")
            if cancel_event and cancel_event.wait(0.20):
                raise get_yt_dlp().utils.DownloadCancelled("Cancelled by user")

        now = time.monotonic()
        status = d.get("status")
        current_downloaded = max(0, int(d.get("downloaded_bytes") or 0))
        current_total = max(0, int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0))
        raw_speed = max(0.0, float(d.get("speed") or 0))
        filename = str(d.get("filename") or d.get("tmpfilename") or "__single_stream__")

        with jobs_lock:
            previous = dict(jobs.get(job_id, {}))
            streams = dict(previous.get("streams") or {})

        # Track each yt-dlp stream independently. A DASH download can have a
        # video stream followed by an audio stream; combining them prevents the
        # progress UI from resetting to 0% when the second stream begins.
        stream = dict(streams.get(filename) or {})
        stream["downloaded"] = current_downloaded
        stream["total"] = max(int(stream.get("total") or 0), current_total)
        stream["speed"] = raw_speed
        streams[filename] = stream

        downloaded = sum(int(v.get("downloaded") or 0) for v in streams.values())
        total = sum(int(v.get("total") or 0) for v in streams.values())

        prev_downloaded = int(previous.get("downloaded") or 0)
        prev_ts = float(previous.get("progress_ts") or 0)
        delta_t = now - prev_ts if prev_ts else 0
        delta_b = downloaded - prev_downloaded
        measured_speed = (delta_b / delta_t) if delta_t > 0 and delta_b >= 0 else 0.0
        stream_speed = sum(float(v.get("speed") or 0) for v in streams.values())
        candidate = measured_speed or raw_speed or stream_speed or float(previous.get("speed") or 0)
        old_speed = float(previous.get("speed") or 0)
        speed = candidate
        if candidate > 0 and old_speed > 0:
            speed = old_speed * 0.65 + candidate * 0.35

        if total > 0:
            progress = downloaded * 100.0 / total
        else:
            progress = float(previous.get("progress") or 0)

        if status == "finished":
            progress, speed, eta = 100.0, 0.0, 0
        elif total > 0 and speed > 0:
            eta = max(0, int(round((total - downloaded) / speed)))
        else:
            eta = previous.get("eta")

        update_job(
            job_id,
            status="downloading" if status == "downloading" else status,
            downloaded=downloaded,
            total=total,
            speed=round(speed, 1),
            eta=eta,
            progress=round(min(100.0, max(0.0, progress)), 2),
            filename=d.get("filename") or previous.get("filename"),
            streams=streams,
            progress_ts=now,
            progress_wall=time.time(),
        )

    return hook


def build_format(payload):
    height = str(payload.get("quality") or "best")
    fps = str(payload.get("fps") or "best")
    container = str(payload.get("format") or "mp4").lower()
    ext = container if container in {"mp4", "mkv", "webm"} else "mp4"

    video_filters = []
    audio_filters = []
    if height.isdigit():
        video_filters.append(f"height<={int(height)}")
    if fps.replace(".", "", 1).isdigit():
        video_filters.append(f"fps<={float(fps):g}")

    vf = "".join(f"[{x}]" for x in video_filters)
    af = "".join(f"[{x}]" for x in audio_filters)

    # Prefer streams that can be muxed directly into the requested container,
    # then fall back to the best compatible streams exposed by the site.
    if ext == "mp4":
        # Prefer H.264 video + AAC/M4A audio for broad Windows compatibility,
        # then fall back to any compatible source streams.
        preferred = (
            f"bestvideo[vcodec^=avc1]{vf}+bestaudio[acodec^=mp4a]"
            f"/bestvideo[ext=mp4]{vf}+bestaudio[ext=m4a]"
            f"/best[ext=mp4]{vf}"
            f"/bestvideo{vf}+bestaudio/best{vf}/best"
        )
    elif ext == "webm":
        # Prefer WebM-compatible streams; VP9/AV1 + Opus are valid WebM.
        preferred = (
            f"bestvideo[ext=webm]{vf}+bestaudio[ext=webm]"
            f"/best[ext=webm]{vf}"
            f"/bestvideo{vf}+bestaudio/best{vf}/best"
        )
    else:  # MKV accepts the widest range of video/audio codecs.
        preferred = f"bestvideo{vf}+bestaudio/best{vf}/best"
    return preferred, ext


def audio_options(fmt: str):
    allowed = {"mp3": ("mp3", "192"), "m4a": ("m4a", None), "opus": ("opus", None), "wav": ("wav", None)}
    return allowed.get(fmt.lower(), ("mp3", "192"))


def download_worker(job_id, payload):
    try:
        url = str(payload.get("url", "")).strip()
        if not valid_url(url):
            raise ValueError("Please enter a valid HTTP/HTTPS URL.")
        dest = safe_path(payload.get("destination"))
        dest.mkdir(parents=True, exist_ok=True)
        mode = payload.get("mode", "video")
        title = payload.get("title") or "Download"
        requested_name = safe_filename(payload.get("filename") or title, "Download")
        # The UI asks for the filename before a download starts.  Keeping the
        # extension supplied by yt-dlp lets post-processing choose the correct
        # final container while preserving the user's chosen base name.
        outtmpl = str(dest / (requested_name + ".%(ext)s"))
        common = {
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [progress_hook(job_id)],
            # Faster fragmented downloads (YouTube DASH and other segmented
            # sources). A single direct file is unaffected by this setting.
            "concurrent_fragment_downloads": 16,
            "buffersize": 16 * 1024 * 1024,
            "socket_timeout": 20,
            "retries": 5,
            "fragment_retries": 5,
            "continuedl": True,
            "overwrites": False,
            "windowsfilenames": True,
            "restrictfilenames": False,
        }

        if mode == "audio":
            audio_fmt, bitrate = audio_options(str(payload.get("format", "mp3")))
            # Prefer a real audio-only stream, but accept a muxed source when a
            # site does not expose separate audio. FFmpegExtractAudio then creates
            # the requested final audio container reliably.
            common["format"] = "bestaudio/best"
            common["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_fmt,
                    **({"preferredquality": bitrate} if bitrate else {}),
                }
            ]
            common["ffmpeg_location"] = str(ROOT / "runtime" / "ffmpeg" / "bin")
            common["postprocessor_args"] = {
                "ExtractAudio": ["-vn"]
            }
        else:
            fmt_expr, merge_ext = build_format(payload)
            common["format"] = fmt_expr
            common["merge_output_format"] = merge_ext
            # Do not force video re-encoding. Re-encoding can make an otherwise
            # fast download take many extra minutes and can make the progress bar
            # appear stuck at 100%. The format selectors above prefer streams that
            # can be muxed directly into the requested container.

        update_job(job_id, status="starting", destination=str(dest), title=title, progress_ts=time.monotonic(), progress_wall=time.time())
        # FFmpeg is initialized only after the job is visible in the manager,
        # keeping application startup fast.
        if not ensure_ffmpeg():
            raise RuntimeError(
                "FFmpeg could not be repaired automatically. Please run the installer again and see logs/app.log."
            )
        yt_dlp = get_yt_dlp()
        with yt_dlp.YoutubeDL(common) as ydl:
            info = ydl.extract_info(url, download=True)
            # At this point network transfer is complete; any remaining work is
            # FFmpeg mux/post-processing. Show this separately instead of making
            # the user think the network download has frozen at 100%.
            update_job(job_id, status="processing", progress=100, speed=0, eta=None)
            prepared = Path(ydl.prepare_filename(info))
            requested_ext = (
                audio_options(str(payload.get("format", "mp3")))[0]
                if mode == "audio"
                else common["merge_output_format"]
            )
            final = resolve_downloaded_file(prepared, dest, mode, requested_ext)

        ctl = controls.get(job_id, {})
        if ctl.get("cancel") and ctl["cancel"].is_set():
            update_job(job_id, status="cancelled")
        else:
            size = final.stat().st_size if final.exists() else 0
            update_job(
                job_id,
                status="finished",
                progress=100,
                downloaded=size,
                total=size,
                path=str(final),
                filename=final.name,
                completed_at=time.time(),
            )
            save_history()
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        if "avformat-" in lowered or "avcodec-" in lowered or "ffmpeg" in lowered and "not found" in lowered:
            message = (
                "FFmpeg failed while processing this media. ClipForge will repair its FFmpeg runtime automatically on the next check. "
                f"Original error: {message}"
            )
        elif "cannot parse data" in lowered and ("facebook" in str(payload.get("url", "")).lower() or "instagram" in str(payload.get("url", "")).lower()):
            message = (
                "This public post could not be parsed by the current site extractor. "
                "The site may have changed its public format or may require an allowed session/cookie. "
                "Try the direct public video URL or update/repair ClipForge, then retry.\n"
                f"Original error: {message}"
            )
        cancelled = "cancelled by user" in lowered
        log.exception("Download job %s failed", job_id)
        update_job(job_id, status="cancelled" if cancelled else "error", error=None if cancelled else message)
    finally:
        controls.pop(job_id, None)


@app.get("/")
def index():
    return send_from_directory(ROOT / "web", "index.html")


@app.get("/api/health")
def health():
    # Health is also the self-healing entry point. This runs once when the UI
    # opens and repairs a missing/broken FFmpeg runtime automatically.
    ffmpeg_ok = ensure_ffmpeg()
    return jsonify(
        {
            "ok": True,
            "ffmpeg": ffmpeg_ok and _ffmpeg_command_works("ffmpeg"),
            "ffprobe": ffmpeg_ok and _ffmpeg_command_works("ffprobe"),
            "yt_dlp": None if _yt_dlp is None else getattr(getattr(_yt_dlp, "version", None), "__version__", None),
            "download_dir": str(DEFAULT_DOWNLOADS),
        }
    )


@app.post("/api/analyze")
def api_analyze():
    data = request.get_json(force=True) or {}
    url = str(data.get("url", "")).strip()
    if not valid_url(url):
        return jsonify({"error": "Enter a valid HTTP/HTTPS URL."}), 400
    try:
        return jsonify(analyze(url))
    except Exception as exc:
        log.exception("Analyze failed for %s", url)
        return jsonify({"error": f"Could not analyze this URL: {exc}"}), 400


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True) or {}
    if not valid_url(str(data.get("url", ""))):
        return jsonify({"error": "Analyze a valid URL before downloading."}), 400
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "downloaded": 0,
            "total": 0,
            "speed": 0,
            "eta": None,
            "progress_ts": 0,
            "progress_wall": time.time(),
            "streams": {},
            "title": data.get("title") or "Download",
            "source": source_name(str(data.get("url", ""))),
            "created_at": time.time(),
            "mode": data.get("mode", "video"),
            "url": data.get("url"),
            "quality": data.get("quality", "best"),
            "fps": data.get("fps", "best"),
            "format": data.get("format", "mp4"),
            "destination": data.get("destination") or str(DEFAULT_DOWNLOADS),
            "filename": data.get("filename") or data.get("title") or "Download",
        }
        controls[job_id] = {"pause": threading.Event(), "cancel": threading.Event()}
    threading.Thread(target=download_worker, args=(job_id, data), daemon=True, name=f"download-{job_id}").start()
    return jsonify({"id": job_id})


@app.get("/api/jobs")
def api_jobs():
    with jobs_lock:
        return jsonify(sorted(jobs.values(), key=lambda x: x.get("created_at", 0), reverse=True)[:100])


@app.post("/api/jobs/<job_id>/pause")
def pause_job(job_id):
    ctl = controls.get(job_id)
    if not ctl:
        return jsonify({"error": "Job is no longer active."}), 404
    ctl["pause"].set()
    update_job(job_id, status="paused")
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/resume")
def resume_job(job_id):
    ctl = controls.get(job_id)
    if not ctl:
        return jsonify({"error": "Job is no longer active."}), 404
    ctl["pause"].clear()
    update_job(job_id, status="resuming")
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    ctl = controls.get(job_id)
    if not ctl:
        return jsonify({"error": "Job is no longer active."}), 404
    ctl["cancel"].set()
    ctl["pause"].clear()
    update_job(job_id, status="cancelling")
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/retry")
def retry_job(job_id):
    with jobs_lock:
        old = jobs.get(job_id)
    if not old:
        return jsonify({"error": "Job not found."}), 404
    if old.get("status") not in {"error", "cancelled"}:
        return jsonify({"error": "Only failed or cancelled jobs can be retried."}), 400
    payload = {
        "url": old.get("url") or old.get("webpage_url"),
        "title": old.get("title"),
        "mode": old.get("mode", "video"),
        "quality": old.get("quality", "best"),
        "fps": old.get("fps", "best"),
        "format": old.get("format", "mp4"),
        "destination": old.get("destination", str(DEFAULT_DOWNLOADS)),
        "filename": old.get("filename") or old.get("title") or "Download",
    }
    if not payload["url"]:
        return jsonify({"error": "Original URL is unavailable; analyze it again."}), 400
    return api_download_internal(payload)


def api_download_internal(data):
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "downloaded": 0,
            "total": 0,
            "speed": 0,
            "eta": None,
            "progress_ts": 0,
            "progress_wall": time.time(),
            "streams": {},
            "title": data.get("title") or "Download",
            "source": source_name(str(data.get("url", ""))),
            "created_at": time.time(),
            "mode": data.get("mode", "video"),
            "url": data.get("url"),
            "quality": data.get("quality", "best"),
            "fps": data.get("fps", "best"),
            "format": data.get("format", "mp4"),
            "destination": data.get("destination") or str(DEFAULT_DOWNLOADS),
            "filename": data.get("filename") or data.get("title") or "Download",
        }
        controls[job_id] = {"pause": threading.Event(), "cancel": threading.Event()}
    threading.Thread(target=download_worker, args=(job_id, data), daemon=True, name=f"download-{job_id}").start()
    return jsonify({"id": job_id})


@app.delete("/api/jobs/<job_id>")
def remove_job(job_id):
    ctl = controls.pop(job_id, None)
    if ctl:
        ctl["cancel"].set()
    with jobs_lock:
        jobs.pop(job_id, None)
    save_history()
    return jsonify({"ok": True})


@app.post("/api/select-folder")
def select_folder():
    # The app is local-only, so a native folder dialog can safely run on the same Windows machine.
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(initialdir=str(DEFAULT_DOWNLOADS), title="Choose download folder")
        root.destroy()
        return jsonify({"path": folder or ""})
    except Exception as exc:
        log.exception("Folder picker failed")
        return jsonify({"error": f"Could not open the folder picker: {exc}"}), 500


@app.get("/api/open")
def api_open():
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "No file was supplied."}), 400
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=True)
    except FileNotFoundError:
        return jsonify({"error": "File not found. The download may have been moved or removed."}), 404
    if not path.is_file():
        return jsonify({"error": "The selected path is not a file."}), 400
    try:
        open_local_path(path)
        return jsonify({"ok": True, "path": str(path)})
    except Exception as exc:
        log.exception("Could not open file %s", path)
        return jsonify({"error": f"Could not open the file: {exc}"}), 500


@app.get("/api/open-folder")
def api_open_folder():
    raw = str(request.args.get("path") or "").strip()
    path = safe_path(raw)
    try:
        # For an explicit folder request, open exactly that folder. Do not
        # silently convert a file path to its parent folder.
        path = path.resolve(strict=True)
    except FileNotFoundError:
        return jsonify({"error": "Folder not found. Choose an existing destination folder first."}), 404
    if not path.is_dir():
        return jsonify({"error": "The selected path is not a folder."}), 400
    try:
        open_local_path(path)
        return jsonify({"ok": True, "path": str(path)})
    except Exception as exc:
        log.exception("Could not open folder %s", path)
        return jsonify({"error": f"Could not open the folder: {exc}"}), 500


# Restore persistent history into the in-memory manager.
for item in load_history():
    if isinstance(item, dict) and item.get("id"):
        jobs[item["id"]] = item


if __name__ == "__main__":
    log.info("Starting ClipForge Media Downloader")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
