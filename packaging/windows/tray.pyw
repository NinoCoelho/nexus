"""Windows tray launcher — functional analog of packaging/macos/Sources/Nexus.

Spawns the bundled CPython interpreter against ``bootstrap.py``, polls for
the server's chosen port (written to ``.port`` next to this file), opens
the default browser when /health succeeds, and surfaces a system-tray
menu with the same actions as the macOS menu-bar app: Open Nexus,
Restart, Show Access Token, Reveal Logs, Open ~/.nexus, Quit.

Launched by ``Nexus.cmd`` via ``pythonw.exe`` so no console window
appears. All long-running work happens off the UI thread; the tray menu
just dispatches.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent

PYTHON_EXE = HERE / "python" / "pythonw.exe"
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

        for f in (PORT_FILE, HOST_FILE):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

        self._log_handle = open(SERVER_LOG, "ab")

        env = os.environ.copy()
        env["NEXUS_PORT_FILE"] = str(PORT_FILE)
        env["PYTHONHOME"] = str(HERE / "python")

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

    def wait_for_ready(self, timeout: float = 300.0) -> int:
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


class UpdateChecker:
    """Background thread that polls the local /update/check endpoint."""

    def __init__(self) -> None:
        self.port: int | None = None
        self.update_available: bool = False
        self.latest_version: str = ""
        self.current_version: str = ""
        self.download_state: str = "idle"
        self.download_progress: float = 0
        self.release_notes: str = ""
        self.html_url: str = ""
        self._stop = threading.Event()

    def configure(self, port: int) -> None:
        self.port = port
        self.check_now()
        threading.Thread(target=self._periodic_check, daemon=True).start()

    def _periodic_check(self) -> None:
        while not self._stop.wait(4 * 3600):
            self.check_now()

    def check_now(self) -> None:
        if self.port is None:
            return
        try:
            url = f"http://127.0.0.1:{self.port}/update/check"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            self.update_available = data.get("update_available", False)
            self.latest_version = data.get("latest", "")
            self.current_version = data.get("current", "")
            self.release_notes = data.get("body", "")
            self.html_url = data.get("html_url", "")
        except Exception:
            log.exception("update check failed")

    def start_download(self) -> None:
        if self.port is None:
            return
        self.download_state = "downloading"
        self.download_progress = 0

        def _do() -> None:
            try:
                url = f"http://127.0.0.1:{self.port}/update/download"
                req = urllib.request.Request(url, method="POST", data=b"")
                with urllib.request.urlopen(req, timeout=600):
                    pass
            except Exception:
                log.exception("update download request failed")
            self._poll_status()

        threading.Thread(target=_do, daemon=True).start()

    def _poll_status(self) -> None:
        while self.port is not None:
            try:
                url = f"http://127.0.0.1:{self.port}/update/status"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    data = json.loads(resp.read())
                self.download_state = data.get("state", "idle")
                self.download_progress = data.get("progress", 0)
                if self.download_state != "downloading":
                    break
            except Exception:
                break
            time.sleep(2)

    def install_update(self) -> None:
        if self.port is None:
            return
        try:
            url = f"http://127.0.0.1:{self.port}/update/install"
            req = urllib.request.Request(url, method="POST", data=b"")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            log.exception("update install request failed")

    def skip_version(self) -> None:
        if self.port is None or not self.latest_version:
            return
        try:
            url = f"http://127.0.0.1:{self.port}/update/skip"
            body = json.dumps({"version": self.latest_version}).encode()
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(req, timeout=5)
            self.update_available = False
        except Exception:
            log.exception("skip version failed")

    def open_release_page(self) -> None:
        if self.html_url:
            webbrowser.open(self.html_url)


def _make_icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(46, 92, 196, 255))
    d.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 255))
    return img


def main() -> int:
    try:
        import pystray
        from pystray import Menu, MenuItem
    except ImportError:
        log.exception("pystray missing — falling back to headless launch")
        ctl = ServerController()
        ctl.launch()
        try:
            port = ctl.wait_for_ready()
            webbrowser.open(f"http://127.0.0.1:{port}/")
            if ctl.process is not None:
                ctl.process.wait()
        finally:
            ctl.terminate()
        return 0

    controller = ServerController()
    updater = UpdateChecker()
    icon = pystray.Icon("Nexus", _make_icon_image(), "Nexus — initializing…")

    state = {"opened_once": False}

    def open_browser(_=None) -> None:
        if controller.port is None:
            return
        webbrowser.open(f"http://127.0.0.1:{controller.port}/")

    def restart(_=None) -> None:
        def _do() -> None:
            controller.terminate()
            try:
                controller.launch()
                port = controller.wait_for_ready()
                webbrowser.open(f"http://127.0.0.1:{port}/")
                icon.title = f"Nexus — running on {controller.bind_host}:{port}"
                updater.configure(port)
            except Exception as exc:
                log.exception("restart failed")
                icon.title = f"Nexus — error: {exc}"
        threading.Thread(target=_do, daemon=True).start()

    def show_token(_=None) -> None:
        import ctypes
        token_path = NEXUS_HOME / "access_token"
        try:
            tok = token_path.read_text().strip()
        except OSError:
            tok = "(not yet generated — start the server first)"
        ctypes.windll.user32.MessageBoxW(
            0, tok, "Nexus access token",
            0x00000040,
        )

    def reveal_logs(_=None) -> None:
        os.startfile(str(LOG_DIR))

    def reveal_state(_=None) -> None:
        NEXUS_HOME.mkdir(parents=True, exist_ok=True)
        os.startfile(str(NEXUS_HOME))

    def check_updates(_=None) -> None:
        updater.check_now()
        if updater.update_available:
            icon.notify(f"Version {updater.latest_version} is available!", "Nexus Update")
        else:
            icon.notify("You're up to date!", "Nexus Update")

    def download_update(_=None) -> None:
        updater.start_download()
        icon.notify("Downloading update…", "Nexus Update")

    def install_update(_=None) -> None:
        updater.install_update()

    def skip_version(_=None) -> None:
        updater.skip_version()
        icon.notify("Version skipped.", "Nexus Update")

    def view_release(_=None) -> None:
        updater.open_release_page()

    def quit_app(_=None) -> None:
        controller.terminate()
        icon.stop()

    def _update_label(item=None) -> str:
        if updater.download_state == "ready":
            return "Install Update & Restart"
        if updater.download_state == "downloading":
            pct = int(updater.download_progress * 100)
            return f"Downloading… {pct}%"
        if updater.update_available:
            return f"● Update Available (v{updater.latest_version})"
        return "Check for Updates…"

    def _update_action(item=None) -> None:
        if updater.download_state == "ready":
            install_update()
        elif updater.download_state == "downloading":
            pass
        elif updater.update_available:
            download_update()
        else:
            check_updates()

    def _skip_visible(item=None) -> bool:
        return updater.update_available and updater.download_state not in ("downloading", "ready")

    def _release_visible(item=None) -> bool:
        return updater.update_available

    icon.menu = Menu(
        MenuItem("Open Nexus", open_browser, default=True),
        Menu.SEPARATOR,
        MenuItem("Restart Server", restart),
        Menu.SEPARATOR,
        MenuItem(_update_label, _update_action),
        MenuItem("Skip This Version", skip_version, visible=_skip_visible),
        MenuItem("View Release Notes…", view_release, visible=_release_visible),
        Menu.SEPARATOR,
        MenuItem("Show Access Token…", show_token),
        MenuItem("Show Logs", reveal_logs),
        MenuItem("Open ~/.nexus", reveal_state),
        Menu.SEPARATOR,
        MenuItem("Quit", quit_app),
    )

    def boot() -> None:
        try:
            controller.launch()
            icon.title = "Nexus — waiting for server…"
            port = controller.wait_for_ready()
            icon.title = f"Nexus — running on {controller.bind_host}:{port}"
            if not state["opened_once"]:
                state["opened_once"] = True
                webbrowser.open(f"http://127.0.0.1:{port}/")
            updater.configure(port)
        except TimeoutError:
            log.error("server did not become ready in time")
            icon.title = "Nexus — failed to start (timeout)"
            icon.notify("Server took too long to start. Check logs.", "Nexus")
        except Exception as exc:
            log.exception("startup failed")
            icon.title = f"Nexus — error: {exc}"

    threading.Thread(target=boot, daemon=True).start()

    try:
        icon.run()
    finally:
        controller.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
