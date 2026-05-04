"""Windows tray launcher — functional analog of packaging/macos/Sources/Nexus.

Spawns the bundled CPython interpreter against ``bootstrap.py``, polls for
the server's chosen port (written to ``.port`` next to this file), opens
an embedded WebView window (via pywebview) when /health succeeds, and
surfaces a system-tray menu with the same actions as the macOS menu-bar
app: Open Nexus, Restart, Show Access Token, Reveal Logs, Open ~/.nexus,
Quit.

Launched by ``Nexus.cmd`` via ``pythonw.exe`` so no console window
appears. All long-running work happens off the UI thread; the tray menu
just dispatches.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# When packaged with PyInstaller (--onefile), ``__file__`` points into the
# temporary extraction dir under %TEMP%\_MEI…, NOT the folder where the
# .exe lives. We need the bundle root (where ``python\``, ``bootstrap.py``,
# etc. sit), so use ``sys.executable`` in frozen mode. In a dev run
# (python tray.pyw), ``__file__`` is the right answer.
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent

PYTHON_EXE = HERE / "python" / "pythonw.exe"  # GUI Python — no console flash
PYTHON_FALLBACK = HERE / "python" / "python.exe"
BOOTSTRAP = HERE / "bootstrap.py"
PORT_FILE = HERE / ".port"
HOST_FILE = HERE / ".host"
NEXUS_HOME = Path.home() / ".nexus"

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Nexus" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SERVER_LOG = LOG_DIR / "server.log"
TRAY_LOG = LOG_DIR / "tray.log"

logging.basicConfig(
    filename=str(TRAY_LOG),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nexus.tray")


class ServerController:
    """Spawns + supervises the bundled Python server. Mirror of the Swift
    ServerController in packaging/macos/Sources/Nexus."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.port: int | None = None
        self.bind_host: str = "127.0.0.1"
        self._log_handle = None

    def launch(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        py = PYTHON_EXE if PYTHON_EXE.is_file() else PYTHON_FALLBACK
        if not py.is_file():
            raise RuntimeError(f"bundled python not found at {py}")
        if not BOOTSTRAP.is_file():
            raise RuntimeError(f"bootstrap.py not found at {BOOTSTRAP}")

        # Stale port file would make waitForReady race against last run's value.
        for f in (PORT_FILE, HOST_FILE):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

        # Append-mode log so a restart preserves history. The Swift host
        # does the same via FileHandle.seekToEndOfFile().
        self._log_handle = open(SERVER_LOG, "ab")

        env = os.environ.copy()
        env["NEXUS_PORT_FILE"] = str(PORT_FILE)
        # python-build-standalone needs PYTHONHOME to find its stdlib.
        env["PYTHONHOME"] = str(HERE / "python")

        # CREATE_NO_WINDOW (0x08000000) hides the console for any child
        # process spawned by bootstrap (e.g. llama-server.exe). Without
        # this, llama.cpp pops a black console window on every restart.
        creationflags = 0x08000000

        self.process = subprocess.Popen(
            [str(py), str(BOOTSTRAP)],
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(HERE),
            env=env,
            creationflags=creationflags,
        )
        log.info("spawned python pid=%d", self.process.pid)

    def wait_for_ready(self, timeout: float = 60.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            port = self._read_port()
            if port is not None:
                self.port = port
                self.bind_host = self._read_host() or "127.0.0.1"
                if self._probe(port):
                    return port
            time.sleep(0.25)
        raise TimeoutError("server did not respond on /health in time")

    def _read_port(self) -> int | None:
        try:
            return int(PORT_FILE.read_text().strip())
        except (OSError, ValueError):
            return None

    def _read_host(self) -> str | None:
        try:
            return HOST_FILE.read_text().strip()
        except OSError:
            return None

    def _probe(self, port: int) -> bool:
        url = f"http://127.0.0.1:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def terminate(self) -> None:
        p = self.process
        if p is None:
            return
        try:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        except OSError:
            pass
        self.process = None
        self.port = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None


# ── Tray UI ─────────────────────────────────────────────────────────────────

def _make_icon_image():
    """Procedurally drawn tray icon — saves shipping a separate .ico asset.
    Matches the macOS hexagon-grid motif loosely (a filled rounded square
    with a small dot, recognizable at 16x16)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(46, 92, 196, 255))
    d.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 255))
    return img


def main() -> int:
    try:
        import pystray  # type: ignore
        from pystray import Menu, MenuItem
    except ImportError:
        log.exception("pystray missing — falling back to headless launch")
        ctl = ServerController()
        ctl.launch()
        try:
            port = ctl.wait_for_ready()
            _open_webview(ctl, port)
        finally:
            ctl.terminate()
        return 0

    controller = ServerController()
    icon = pystray.Icon("Nexus", _make_icon_image(), "Nexus")
    webview_state = {"window": None, "started": False, "lock": threading.Lock()}

    def open_nexus(_=None) -> None:
        if controller.port is None:
            return
        _open_webview(controller, controller.port, webview_state)

    def restart(_=None) -> None:
        def _do() -> None:
            controller.terminate()
            try:
                controller.launch()
                port = controller.wait_for_ready()
                _open_webview(controller, port, webview_state)
                icon.title = f"Nexus — running on {controller.bind_host}:{port}"
            except Exception as exc:  # noqa: BLE001
                log.exception("restart failed")
                icon.title = f"Nexus — error: {exc}"
        threading.Thread(target=_do, daemon=True).start()

    def reveal_logs(_=None) -> None:
        os.startfile(str(LOG_DIR))  # noqa: S606

    def reveal_state(_=None) -> None:
        NEXUS_HOME.mkdir(parents=True, exist_ok=True)
        os.startfile(str(NEXUS_HOME))  # noqa: S606

    def quit_app(_=None) -> None:
        controller.terminate()
        icon.stop()

    icon.menu = Menu(
        MenuItem("Open Nexus", open_nexus, default=True),
        Menu.SEPARATOR,
        MenuItem("Restart Server", restart),
        Menu.SEPARATOR,
        MenuItem("Show Logs", reveal_logs),
        MenuItem("Open Vault Folder", reveal_state),
        Menu.SEPARATOR,
        MenuItem("Quit", quit_app),
    )

    state = {"opened_once": False}

    def boot() -> None:
        try:
            controller.launch()
            port = controller.wait_for_ready()
            icon.title = f"Nexus — running on {controller.bind_host}:{port}"
            if not state["opened_once"]:
                state["opened_once"] = True
                _open_webview(controller, port, webview_state)
        except Exception as exc:  # noqa: BLE001
            log.exception("startup failed")
            icon.title = f"Nexus — error: {exc}"

    threading.Thread(target=boot, daemon=True).start()

    try:
        icon.run()
    finally:
        controller.terminate()
    return 0


def _open_webview(
    controller: ServerController,
    port: int,
    state: dict | None = None,
) -> None:
    """Open or re-focus the embedded webview window.

    pywebview must run its event loop on the main thread via
    ``webview.start()``. Since the tray icon already owns the main thread
    (pystray runs a blocking Windows message loop), we run webview on a
    dedicated thread. ``webview.start()`` blocks until all windows are
    closed, so we keep a long-lived thread alive that re-creates windows
    as needed.
    """
    try:
        import webview  # type: ignore
    except ImportError:
        import webbrowser
        log.warning("pywebview not available — falling back to system browser")
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return

    url = f"http://127.0.0.1:{port}/"

    if state is not None:
        with state["lock"]:
            win = state.get("window")
            if win is not None:
                try:
                    win.load_url(url)
                    win.restore()
                    return
                except Exception:  # noqa: BLE001
                    state["window"] = None

    def _run() -> None:
        win = webview.create_window("Nexus", url, width=1280, height=800)
        if state is not None:
            with state["lock"]:
                state["window"] = win
        webview.start()

    if state is not None and not state.get("started"):
        state["started"] = True
        t = threading.Thread(target=_run, daemon=True)
        t.start()
    elif state is not None:
        with state["lock"]:
            win = webview.create_window("Nexus", url, width=1280, height=800)
            state["window"] = win
    else:
        _run()


if __name__ == "__main__":
    sys.exit(main())
