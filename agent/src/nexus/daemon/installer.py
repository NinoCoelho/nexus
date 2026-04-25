"""ServiceInstaller — system service installation for Nexus."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from rich.console import Console


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
