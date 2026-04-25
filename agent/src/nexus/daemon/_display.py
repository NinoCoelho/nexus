"""Display mixin for DaemonManager — show_status and show_logs."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
from rich.table import Table

if TYPE_CHECKING:
    from rich.console import Console


class DaemonDisplayMixin:
    """Rich-console display methods for DaemonManager.

    Expects the host class to have: ``console``, ``log_file``,
    ``ui_log_file``, ``pid_file``, ``ui_pid_file``, ``get_pid()``,
    ``get_ui_pid()``.
    """

    console: "Console"
    log_file: Path
    ui_log_file: Path
    pid_file: Path
    ui_pid_file: Path

    def get_pid(self) -> int | None: ...  # provided by DaemonManager
    def get_ui_pid(self) -> int | None: ...

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
