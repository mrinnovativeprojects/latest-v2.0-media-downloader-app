import atexit
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

# Give Windows a stable application identity so the taskbar groups ClipForge
# under the ClipForge icon instead of the Python interpreter icon. This must
# run before any GUI/window is created.
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ClipForge.MediaDownloader"
        )
    except Exception:
        pass

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# Prefer the installer-managed FFmpeg runtime. This keeps the app independent
# of whether Windows refreshed PATH after a package-manager installation.
FFMPEG_DIR = ROOT / "runtime" / "ffmpeg" / "bin"
if FFMPEG_DIR.is_dir():
    os.environ["PATH"] = str(FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")
else:
    # Do not import imageio-ffmpeg during GUI startup. It is relatively heavy
    # and was a measurable source of launch delay. The backend loads it lazily
    # only if an actual download needs FFmpeg.
    pass

from werkzeug.serving import make_server
logging.basicConfig(filename=str(LOG_DIR / "desktop.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clipforge.desktop")
HOST = "127.0.0.1"
PREFERRED_PORT = 8765
server = None

def choose_server(flask_app):
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 20):
        try:
            candidate = make_server(HOST, port, flask_app, threaded=True)
            log.info("Local service bound to %s:%s", HOST, port)
            return candidate, port
        except OSError:
            continue
    raise RuntimeError("No free local port was available for ClipForge.")

def shutdown_server():
    if server is not None:
        try: server.shutdown()
        except Exception: log.exception("Error shutting down local service")

def acquire_single_instance():
    """Prevent duplicate ClipForge windows/processes on Windows."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "Local\\ClipForge.MediaDownloader.Singleton")
        if not mutex:
            return None
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "ClipForge Media Downloader")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            kernel32.CloseHandle(mutex)
            return False
        return mutex
    except Exception:
        log.exception("Single-instance guard could not be initialized")
        return None


def release_single_instance(mutex):
    if mutex and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(mutex)
            ctypes.windll.kernel32.CloseHandle(mutex)
        except Exception:
            pass

def show_fatal(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "ClipForge", 0x10)
    except Exception: pass

def main():
    global server
    mutex = acquire_single_instance()
    if mutex is False:
        return

    # Start the Flask backend and import pywebview in parallel.  The previous
    # startup path imported yt-dlp/Flask first, waited for an HTTP health check,
    # and only then imported pywebview.  That serialized the two heaviest startup
    # operations and caused the visible 2-5 second launch delay.
    ready = threading.Event()
    state = {}

    def bootstrap_backend():
        try:
            from app import app as flask_app
            state["app"] = flask_app
            state["server"], state["port"] = choose_server(flask_app)
            server = state["server"]
            thread = threading.Thread(target=server.serve_forever, name="ClipForgeServer", daemon=True)
            thread.start()
            state["thread"] = thread
        except Exception as exc:
            state["error"] = exc
        finally:
            ready.set()

    backend_thread = threading.Thread(target=bootstrap_backend, name="ClipForgeBackendBootstrap", daemon=True)
    backend_thread.start()

    # Import the native GUI wrapper while the local backend is starting.
    import webview

    # Backend startup is intentionally lightweight; wait only as long as needed
    # for the local socket. No FFmpeg/yt-dlp import is performed on this path.
    if not ready.wait(2):
        raise RuntimeError("ClipForge local service startup timed out.")
    if state.get("error"):
        raise state["error"]

    server = state["server"]
    port = state["port"]
    atexit.register(shutdown_server)
    atexit.register(release_single_instance, mutex)

    webview.create_window(
        "ClipForge Media Downloader",
        f"http://{HOST}:{port}/",
        width=1280,
        height=820,
        min_size=(1000, 650),
        confirm_close=True,
        hidden=False,
    )
    webview.start(gui="edgechromium", debug=False, icon=str(ROOT / "ClipForge.ico"), http_server=False)

if __name__ == "__main__":
    try: main()
    except Exception as exc:
        log.exception("ClipForge desktop startup failed")
        show_fatal(f"ClipForge could not start.\n\n{exc}\n\nSee: {LOG_DIR / 'desktop.log'}")
