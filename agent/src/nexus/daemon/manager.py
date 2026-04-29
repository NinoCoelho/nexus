"""DaemonManager — start/stop/status the Nexus backend and frontend processes."""

from __future__ import annotations

import platform
import psutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

from ..config import PORT
from ._display import DaemonDisplayMixin


class DaemonManager(DaemonDisplayMixin):
    """Manages the Nexus daemon process."""

    def __init__(self):
        self.console = Console()
        self.data_dir = Path.home() / ".nexus"
        self.pid_file = self.data_dir / "nexus-daemon.pid"
        self.ui_pid_file = self.data_dir / "nexus-frontend.pid"
        self.log_file = self.data_dir / "nexus-daemon.log"
        self.ui_log_file = self.data_dir / "nexus-frontend.log"
        self.data_dir.mkdir(exist_ok=True)

    def get_pid(self) -> Optional[int]:
        """Get PID from PID file if it exists and process is running."""
        if not self.pid_file.exists():
            return None

        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())

            if psutil.pid_exists(pid):
                return pid
            else:
                self.pid_file.unlink(missing_ok=True)
                return None
        except (ValueError, FileNotFoundError, psutil.NoSuchProcess):
            return None

    def get_ui_pid(self) -> Optional[int]:
        if not self.ui_pid_file.exists():
            return None
        try:
            with open(self.ui_pid_file, 'r') as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                return pid
            else:
                self.ui_pid_file.unlink(missing_ok=True)
                return None
        except (ValueError, FileNotFoundError, psutil.NoSuchProcess):
            return None

    def is_running(self) -> bool:
        """Check if daemon is currently running."""
        return self.get_pid() is not None

    def _untracked_listener_pid(self, port: int = PORT) -> Optional[int]:
        """Return the PID of a process listening on ``port`` that is NOT the
        one recorded in our pidfile, or None. This catches the case where a
        ``nexus serve`` was started outside the daemon wrapper (or survived
        after the pidfile was clobbered) — without this the user sees
        "Stopped" even though the server is healthy on 18989.
        """
        tracked = None
        if self.pid_file.exists():
            try:
                tracked = int(self.pid_file.read_text().strip())
            except (ValueError, OSError):
                tracked = None
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status != psutil.CONN_LISTEN:
                    continue
                if conn.laddr and conn.laddr.port == port and conn.pid and conn.pid != tracked:
                    return conn.pid
        except (psutil.AccessDenied, PermissionError):
            # Fall through to the lsof fallback below.
            pass
        # Fallback for macOS where psutil.net_connections returns nothing for
        # unprivileged callers. lsof exposes listening sockets without root.
        try:
            out = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
                capture_output=True, text=True, timeout=3,
            )
            for line in out.stdout.splitlines():
                if line.startswith("p"):
                    pid = int(line[1:])
                    if pid != tracked:
                        return pid
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def get_status(self) -> str:
        """Get current daemon status."""
        if self.is_running():
            pid = self.get_pid()
            assert pid is not None
            try:
                process = psutil.Process(pid)
                return f"Running (PID: {pid}, Started: {time.ctime(process.create_time())})"
            except psutil.NoSuchProcess:
                return "Stopped (stale PID file)"
        # Not tracked — but something may still be listening on the API port.
        orphan = self._untracked_listener_pid()
        if orphan is not None:
            return (
                f"Stopped (daemon not tracked), but an untracked `nexus serve` "
                f"is listening on port {PORT} (PID: {orphan}). "
                f"Run `nexus daemon stop` or kill PID {orphan} before starting."
            )
        return "Stopped"

    def start(self, port: int = PORT, detach: bool = True) -> bool:
        """Start the daemon process.

        Always binds to ``127.0.0.1``. Remote access is intentionally only
        supported through a tunnel (``nexus tunnel start``), which routes
        through the auth-aware login flow. Cloudflared, tailscale, etc. are
        all clients that connect *to* the loopback bind, so they don't need a
        non-loopback listener either.
        """
        host = "127.0.0.1"
        if self.is_running():
            self.console.print("[red]Daemon is already running.[/red]")
            return False

        cmd = [sys.executable, "-c", f"""
import sys
import os
import time
from pathlib import Path

log_file = Path("{self.log_file}")
log_file.parent.mkdir(parents=True, exist_ok=True)

pid_file = Path("{self.pid_file}")
with open(pid_file, 'w') as f:
    f.write(str(os.getpid()))

sys.stdout = open(log_file, 'a')
sys.stderr = open(log_file, 'a')

try:
    from nexus.main import main
    main()
except Exception as e:
    print(f"Daemon error: {{e}}", file=sys.stderr)
    raise
finally:
    if pid_file.exists():
        pid_file.unlink()
"""]

        try:
            if detach:
                if platform.system() == "Windows":
                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                        close_fds=True
                    )
                else:
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        close_fds=True
                    )

                time.sleep(3)

                if self.pid_file.exists():
                    with open(self.pid_file, 'r') as f:
                        pid = int(f.read().strip())

                    if psutil.pid_exists(pid):
                        self.console.print(f"[green]Backend daemon started (PID: {pid})[/green]")
                        self.console.print(f"[dim]Logs: {self.log_file}[/dim]")
                    else:
                        self.console.print("[red]Failed to start daemon — process exited[/red]")
                        self.pid_file.unlink(missing_ok=True)
                        return False
                else:
                    self.console.print("[red]Failed to start daemon — PID file not created[/red]")
                    return False
            else:
                subprocess.Popen(
                    cmd,
                    stdout=open(self.log_file, 'a'),
                    stderr=subprocess.STDOUT
                )

                time.sleep(1)

                if self.pid_file.exists():
                    with open(self.pid_file, 'r') as f:
                        pid = int(f.read().strip())
                    self.console.print(f"[green]Backend daemon started in foreground (PID: {pid})[/green]")
                    self.console.print(f"[dim]Logs: {self.log_file}[/dim]")
                else:
                    self.console.print("[red]Failed to start daemon — PID file not created[/red]")
                    return False

            self._start_frontend()
            return True

        except Exception as e:
            self.console.print(f"[red]Error starting daemon: {e}[/red]")
            return False

    def _start_frontend(self) -> None:
        from ..config import get_frontend_dir
        import shutil

        frontend_dir = get_frontend_dir()
        if frontend_dir is None:
            return

        npm = shutil.which("npm")
        if npm is None:
            self.console.print("[yellow]npm not found — skipping frontend.[/yellow]")
            return

        subprocess.run([npm, "install"], cwd=str(frontend_dir), check=True, capture_output=True)

        log_fh = open(self.ui_log_file, 'a')
        # stdin=DEVNULL so Vite's readline-based keyboard-shortcut handler
        # ("press h + enter") sees a non-TTY and stays disabled — otherwise it
        # crashes with `read EIO` when the launching terminal closes.
        fp = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=str(frontend_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
        )

        with open(self.ui_pid_file, 'w') as f:
            f.write(str(fp.pid))

        self.console.print(f"[green]Frontend dev server started (PID: {fp.pid})[/green]")
        self.console.print(f"[dim]Logs: {self.ui_log_file}[/dim]")

    def stop(self) -> bool:
        """Stop the daemon process."""
        self._stop_process(self.ui_pid_file, "Frontend")
        result = self._stop_process(self.pid_file, "Backend")
        return result

    def _stop_process(self, pid_file: Path, label: str) -> bool:
        if not pid_file.exists():
            return True

        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
        except (ValueError, FileNotFoundError):
            pid_file.unlink(missing_ok=True)
            return True

        if not psutil.pid_exists(pid):
            pid_file.unlink(missing_ok=True)
            self.console.print(f"[yellow]{label} is not running.[/yellow]")
            return True

        try:
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                self.console.print(f"[yellow]{label} graceful shutdown failed, forcing…[/yellow]")
                process.kill()
                process.wait()

            pid_file.unlink(missing_ok=True)
            self.console.print(f"[green]{label} stopped successfully[/green]")
            return True

        except psutil.NoSuchProcess:
            pid_file.unlink(missing_ok=True)
            return True
        except Exception as e:
            self.console.print(f"[red]Error stopping {label}: {e}[/red]")
            return False

    def restart(self, port: int = PORT) -> bool:
        """Restart the daemon process. Always binds to 127.0.0.1."""
        self.stop()
        time.sleep(1)  # Give it a moment to stop
        return self.start(port=port)

