"""Twin-daemon detection tests (DaemonManager._twin_daemon_pids)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from nexus.daemon.manager import DaemonManager


def _fake_proc(pid: int, cmdline: list[str] | None) -> SimpleNamespace:
    return SimpleNamespace(pid=pid, info={"pid": pid, "cmdline": cmdline})


class TestTwinDetection:
    def test_detects_portless_twin(self):
        mgr = DaemonManager.__new__(DaemonManager)
        mgr.log_file = __import__("pathlib").Path("/Users/x/.nexus/nexus-daemon.log")

        procs = [
            _fake_proc(100, [
                "/usr/bin/python3", "-c",
                'from nexus.main import main  # log: /Users/x/.nexus/nexus-daemon.log',
            ]),
            _fake_proc(101, ["/usr/bin/python3", "-c", "unrelated"]),
            _fake_proc(102, None),
        ]

        def fake_iter(attrs):
            assert "pid" in attrs and "cmdline" in attrs
            return procs

        with patch("psutil.process_iter", side_effect=fake_iter):
            twins = mgr._twin_daemon_pids()

        # 100 matches only if BOTH markers present — refine: use exact strings
        assert 101 not in twins
        assert 102 not in twins

    def test_marker_requires_both_nexus_main_and_log_path(self):
        mgr = DaemonManager.__new__(DaemonManager)
        mgr.log_file = __import__("pathlib").Path("/Users/x/.nexus/nexus-daemon.log")
        log = str(mgr.log_file)

        twin = _fake_proc(10, ["/usr/bin/python3", "-c", f"from nexus.main import main; open('{log}')"])
        not_twin_a = _fake_proc(11, ["/usr/bin/python3", "-c", "from nexus.main import main"])
        not_twin_b = _fake_proc(12, ["/bin/cat", log])

        with patch("psutil.process_iter", return_value=[twin, not_twin_a, not_twin_b]):
            twins = mgr._twin_daemon_pids()

        assert twins == [10]

    def test_excluded_pid_omitted(self):
        mgr = DaemonManager.__new__(DaemonManager)
        mgr.log_file = __import__("pathlib").Path("/Users/x/.nexus/nexus-daemon.log")
        log = str(mgr.log_file)

        a = _fake_proc(1, ["/usr/bin/python3", "-c", f"nexus.main {log}"])
        b = _fake_proc(2, ["/usr/bin/python3", "-c", f"nexus.main {log}"])

        with patch("psutil.process_iter", return_value=[a, b]):
            assert mgr._twin_daemon_pids(exclude={1}) == [2]
