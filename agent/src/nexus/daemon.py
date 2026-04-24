"""Daemon management for Nexus."""

from __future__ import annotations

import platform
import psutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from .config import PORT


class DaemonManager:
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
    
    def start(self, host: str = "127.0.0.1", port: int = PORT, detach: bool = True) -> bool:
        """Start the daemon process."""
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
        from .config import get_frontend_dir
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
        fp = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=str(frontend_dir),
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
    
    def restart(self, host: str = "127.0.0.1", port: int = PORT) -> bool:
        """Restart the daemon process."""
        self.stop()
        time.sleep(1)  # Give it a moment to stop
        return self.start(host, port)
    
    def show_status(self) -> None:
        """Show daemon status."""
        table = Table(title="Nexus Daemon Status")
        table.add_column("Component", style="cyan")
        table.add_column("Status")
        table.add_column("Details")
        
        backend_pid = self.get_pid()
        if backend_pid:
            try:
                process = psutil.Process(backend_pid)
                table.add_row(
                    "Backend",
                    "[green]Running[/green]",
                    f"PID {backend_pid} | {process.memory_info().rss / 1024 / 1024:.1f} MB | started {time.ctime(process.create_time())}",
                )
            except psutil.NoSuchProcess:
                table.add_row("Backend", "[yellow]Stale PID[/yellow]", "")
        else:
            table.add_row("Backend", "[dim]Stopped[/dim]", "")
        
        ui_pid = self.get_ui_pid()
        if ui_pid:
            try:
                process = psutil.Process(ui_pid)
                table.add_row(
                    "Frontend",
                    "[green]Running[/green]",
                    f"PID {ui_pid} | started {time.ctime(process.create_time())}",
                )
            except psutil.NoSuchProcess:
                table.add_row("Frontend", "[yellow]Stale PID[/yellow]", "")
        else:
            table.add_row("Frontend", "[dim]Stopped[/dim]", "")
        
        table.add_row("Log (backend)", "", str(self.log_file))
        table.add_row("Log (frontend)", "", str(self.ui_log_file))
        table.add_row("PID files", "", f"{self.pid_file}, {self.ui_pid_file}")
        
        self.console.print(table)
    
    def show_logs(self, lines: int = 50, follow: bool = False) -> None:
        """Show daemon logs."""
        if not self.log_file.exists():
            self.console.print("[yellow]No log file found.[/yellow]")
            return
        
        try:
            with open(self.log_file, 'r') as f:
                if follow:
                    # Follow mode (like tail -f)
                    import select
                    import tty
                    import termios
                    
                    # Go to end of file
                    f.seek(0, 2)
                    
                    # Get last N lines first
                    f.seek(0, 0)
                    all_lines = f.readlines()
                    last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
                    
                    for line in last_lines:
                        self.console.print(line.rstrip())
                    
                    # Set up for following
                    old_settings = termios.tcgetattr(sys.stdin)
                    try:
                        tty.setraw(sys.stdin.fileno())
                        
                        while True:
                            line = f.readline()
                            if line:
                                self.console.print(line.rstrip(), end='\n')
                            else:
                                # Check for keypress to exit
                                if select.select([sys.stdin], [], [], 0.1)[0]:
                                    if sys.stdin.read(1) == 'q':
                                        break
                                time.sleep(0.1)
                    finally:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                else:
                    # Show last N lines
                    lines_list = f.readlines()
                    last_lines = lines_list[-lines:] if len(lines_list) > lines else lines_list
                    
                    for line in last_lines:
                        self.console.print(line.rstrip())
                    
                    if len(lines_list) > lines:
                        self.console.print(f"[dim]... showing last {lines} of {len(lines_list)} lines ...[/dim]")
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.console.print(f"[red]Error reading logs: {e}[/red]")


class ServiceInstaller:
    """Handles system service installation."""
    
    def __init__(self):
        self.console = Console()
        self.system = platform.system()
        self.data_dir = Path.home() / ".nexus"
        
    def get_service_name(self) -> str:
        """Get platform-specific service name."""
        return "nexus-daemon"
    
    def install_service(self, user: bool = True) -> bool:
        """Install Nexus as a system service."""
        if self.system == "Linux":
            return self._install_systemd(user)
        elif self.system == "Darwin":
            return self._install_launchd(user)
        elif self.system == "Windows":
            return self._install_windows_service(user)
        else:
            self.console.print(f"[red]Unsupported system: {self.system}[/red]")
            return False
    
    def _install_systemd(self, user: bool = True) -> bool:
        """Install systemd service (Linux)."""
        service_name = self.get_service_name()
        
        if user:
            service_dir = Path.home() / ".config" / "systemd" / "user"
            service_file = service_dir / f"{service_name}.service"
        else:
            service_dir = Path("/etc/systemd/system")
            service_file = service_dir / f"{service_name}.service"
        
        service_content = f"""[Unit]
Description=Nexus AI Agent Daemon
After=network.target

[Service]
Type=simple
ExecStart={sys.executable} -m nexus.main
WorkingDirectory={self.data_dir}
Restart=always
RestartSec=3
StandardOutput=file:{self.data_dir}/nexus-daemon.log
StandardError=file:{self.data_dir}/nexus-daemon.log

[Install]
WantedBy=multi-user.target
"""
        
        try:
            service_dir.mkdir(parents=True, exist_ok=True)
            with open(service_file, 'w') as f:
                f.write(service_content)
            
            # Reload systemd
            if user:
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
                subprocess.run(["systemctl", "--user", "enable", service_name], check=True)
                self.console.print("[green]User service installed successfully[/green]")
                self.console.print(f"[dim]Run: systemctl --user start {service_name}[/dim]")
            else:
                subprocess.run(["systemctl", "daemon-reload"], check=True)
                subprocess.run(["systemctl", "enable", service_name], check=True)
                self.console.print("[green]System service installed successfully[/green]")
                self.console.print(f"[dim]Run: systemctl start {service_name}[/dim]")
            
            return True
            
        except subprocess.CalledProcessError as e:
            self.console.print(f"[red]Failed to install systemd service: {e}[/red]")
            return False
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
            return False
    
    def _install_launchd(self, user: bool = True) -> bool:
        """Install launchd service (macOS)."""
        service_name = self.get_service_name()
        
        if user:
            plist_dir = Path.home() / "Library" / "LaunchAgents"
        else:
            plist_dir = Path("/Library" / "LaunchDaemons")
        
        plist_file = plist_dir / f"com.nexus.{service_name}.plist"
        
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nexus.{service_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>nexus.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{self.data_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{self.data_dir}/nexus-daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{self.data_dir}/nexus-daemon.log</string>
</dict>
</plist>
"""
        
        try:
            plist_dir.mkdir(parents=True, exist_ok=True)
            with open(plist_file, 'w') as f:
                f.write(plist_content)
            
            if user:
                subprocess.run(["launchctl", "load", str(plist_file)], check=True)
                self.console.print("[green]Launch agent installed successfully[/green]")
                self.console.print(f"[dim]Run: launchctl start com.nexus.{service_name}[/dim]")
            else:
                subprocess.run(["launchctl", "load", str(plist_file)], check=True)
                self.console.print("[green]Launch daemon installed successfully[/green]")
                self.console.print(f"[dim]Run: launchctl start com.nexus.{service_name}[/dim]")
            
            return True
            
        except subprocess.CalledProcessError as e:
            self.console.print(f"[red]Failed to install launchd service: {e}[/red]")
            return False
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
            return False
    
    def _install_windows_service(self, user: bool = True) -> bool:
        """Install Windows service."""
        self.console.print("[yellow]Windows service installation requires NSSM (Non-Sucking Service Manager)[/yellow]")
        self.console.print("[yellow]Please install NSSM first: https://nssm.cc/[/yellow]")
        self.console.print("[yellow]Then run: nssm install nexus-daemon python -m nexus.main[/yellow]")
        return False
    
    def uninstall_service(self, user: bool = True) -> bool:
        """Uninstall system service."""
        if self.system == "Linux":
            return self._uninstall_systemd(user)
        elif self.system == "Darwin":
            return self._uninstall_launchd(user)
        elif self.system == "Windows":
            self.console.print("[yellow]Use NSSM to uninstall Windows service[/yellow]")
            return False
        else:
            self.console.print(f"[red]Unsupported system: {self.system}[/red]")
            return False
    
    def _uninstall_systemd(self, user: bool = True) -> bool:
        """Uninstall systemd service."""
        service_name = self.get_service_name()
        
        try:
            if user:
                subprocess.run(["systemctl", "--user", "stop", service_name], check=False)
                subprocess.run(["systemctl", "--user", "disable", service_name], check=True)
                service_file = Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
            else:
                subprocess.run(["systemctl", "stop", service_name], check=False)
                subprocess.run(["systemctl", "disable", service_name], check=True)
                service_file = Path("/etc/systemd/system") / f"{service_name}.service"
            
            if service_file.exists():
                service_file.unlink()
            
            if user:
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            else:
                subprocess.run(["systemctl", "daemon-reload"], check=True)
            
            self.console.print("[green]Systemd service uninstalled successfully[/green]")
            return True
            
        except subprocess.CalledProcessError as e:
            self.console.print(f"[red]Failed to uninstall systemd service: {e}[/red]")
            return False
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
            return False
    
    def _uninstall_launchd(self, user: bool = True) -> bool:
        """Uninstall launchd service."""
        service_name = self.get_service_name()
        
        if user:
            plist_file = Path.home() / "Library" / "LaunchAgents" / f"com.nexus.{service_name}.plist"
        else:
            plist_file = Path("/Library" / "LaunchDaemons") / f"com.nexus.{service_name}.plist"
        
        try:
            if plist_file.exists():
                subprocess.run(["launchctl", "unload", str(plist_file)], check=False)
                plist_file.unlink()
            
            self.console.print("[green]Launchd service uninstalled successfully[/green]")
            return True
            
        except subprocess.CalledProcessError as e:
            self.console.print(f"[red]Failed to uninstall launchd service: {e}[/red]")
            return False
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
            return False


# Global instances
daemon_manager = DaemonManager()
service_installer = ServiceInstaller()