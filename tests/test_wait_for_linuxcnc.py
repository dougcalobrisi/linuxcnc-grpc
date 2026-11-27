"""
Unit tests for scripts/wait-for-linuxcnc.py, the readiness check used by the e2e job.
"""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class TestWaitForLinuxCNCScript:
    """scripts/wait-for-linuxcnc.py: the overall timeout covers every child process."""

    @pytest.fixture
    def wait_script(self):
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "wait-for-linuxcnc.py"
        script_spec = importlib.util.spec_from_file_location("wait_for_linuxcnc_script", script_path)
        script_module = importlib.util.module_from_spec(script_spec)
        script_spec.loader.exec_module(script_module)
        return script_module

    @staticmethod
    def _fake_clock():
        clock = SimpleNamespace(now=1000.0)
        clock.monotonic = lambda: clock.now
        clock.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
        return clock

    def test_a_hanging_module_check_ends_at_the_overall_timeout(self, wait_script):
        child_timeouts = []

        def child_that_hangs(command, **run_arguments):
            child_timeouts.append(run_arguments.get("timeout"))
            raise subprocess.TimeoutExpired(command, run_arguments.get("timeout"))

        fake_subprocess = SimpleNamespace(
            run=child_that_hangs, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", self._fake_clock()):
            became_ready = wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5)

        assert became_ready is False
        # Only the module check ran, and it was not allowed more than the overall timeout.
        assert len(child_timeouts) == 1
        assert child_timeouts[0] is not None
        assert 0 < child_timeouts[0] <= 1.0

    def test_module_check_is_capped_at_the_probe_timeout(self, wait_script):
        child_timeouts = []

        def child_that_fails(command, **run_arguments):
            child_timeouts.append(run_arguments.get("timeout"))
            return MagicMock(returncode=1, stdout="", stderr="")

        fake_subprocess = SimpleNamespace(
            run=child_that_fails, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", self._fake_clock()):
            became_ready = wait_script.wait_for_linuxcnc(timeout=600.0, interval=0.5)

        assert became_ready is False
        assert child_timeouts == [wait_script.PROBE_TIMEOUT_SECONDS]

    def test_time_spent_on_the_module_check_counts_towards_the_timeout(self, wait_script):
        clock = self._fake_clock()
        child_commands = []

        def child_whose_import_is_slow(command, **run_arguments):
            child_commands.append(command[2])
            if command[2] == "import linuxcnc":
                clock.now += 5.0
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="1 1\n", stderr="")

        fake_subprocess = SimpleNamespace(
            run=child_whose_import_is_slow, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", clock):
            became_ready = wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5)

        # The slow check used the whole second: no probe may start after it.
        assert became_ready is False
        assert child_commands == ["import linuxcnc"]

    def test_ready_linuxcnc_is_still_reported(self, wait_script):
        def child_that_finds_linuxcnc(command, **run_arguments):
            return MagicMock(returncode=0, stdout="1 1\n", stderr="")

        fake_subprocess = SimpleNamespace(
            run=child_that_finds_linuxcnc, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", self._fake_clock()):
            assert wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5) is True

    def test_each_probe_gets_only_the_time_that_is_left(self, wait_script):
        clock = self._fake_clock()
        probe_timeouts = []

        def child_that_never_finds_linuxcnc(command, **run_arguments):
            if command[2] == "import linuxcnc":
                clock.now += 0.9
                return MagicMock(returncode=0, stdout="", stderr="")
            # A probe that hangs: it uses up exactly the time it was given.
            probe_timeouts.append(run_arguments.get("timeout"))
            clock.now += run_arguments.get("timeout")
            raise subprocess.TimeoutExpired(command, run_arguments.get("timeout"))

        fake_subprocess = SimpleNamespace(
            run=child_that_never_finds_linuxcnc, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", clock):
            became_ready = wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5)

        assert became_ready is False
        # 0.9 s went on the module check, so the one probe got the remaining 0.1 s,
        # not the fixed probe timeout.
        assert probe_timeouts == [pytest.approx(0.1)]
        assert clock.now - 1000.0 == pytest.approx(1.0)

    def test_probe_timeout_is_capped_at_the_probe_timeout_when_time_is_plentiful(self, wait_script):
        probe_timeouts = []

        def child_that_finds_linuxcnc(command, **run_arguments):
            if command[2] != "import linuxcnc":
                probe_timeouts.append(run_arguments.get("timeout"))
            return MagicMock(returncode=0, stdout="1 1\n", stderr="")

        fake_subprocess = SimpleNamespace(
            run=child_that_finds_linuxcnc, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", self._fake_clock()):
            assert wait_script.wait_for_linuxcnc(timeout=600.0, interval=0.5) is True

        assert probe_timeouts == [wait_script.PROBE_TIMEOUT_SECONDS]

    def test_an_answer_after_the_deadline_is_not_reported_as_ready(self, wait_script):
        clock = self._fake_clock()
        probe_count = 0

        def child_that_answers_too_late(command, **run_arguments):
            nonlocal probe_count
            if command[2] == "import linuxcnc":
                clock.now += 0.9
                return MagicMock(returncode=0, stdout="", stderr="")
            probe_count += 1
            # The child overruns its timeout (process start and teardown are not
            # covered by it) and still returns a ready state.
            clock.now += 5.0
            return MagicMock(returncode=0, stdout="1 1\n", stderr="")

        fake_subprocess = SimpleNamespace(
            run=child_that_answers_too_late, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", clock):
            became_ready = wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5)

        assert became_ready is False
        assert probe_count == 1

    def test_the_wait_between_attempts_stops_at_the_deadline(self, wait_script):
        clock = self._fake_clock()
        sleep_durations = []

        def recording_sleep(seconds):
            sleep_durations.append(seconds)
            clock.now += seconds

        clock.sleep = recording_sleep

        def child_where_linuxcnc_is_not_ready(command, **run_arguments):
            clock.now += 0.3
            if command[2] == "import linuxcnc":
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="0 0\n", stderr="")

        fake_subprocess = SimpleNamespace(
            run=child_where_linuxcnc_is_not_ready, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", clock):
            became_ready = wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5)

        assert became_ready is False
        # Module check to 0.3 s, probe to 0.6 s, then only 0.4 s of the 0.5 s interval.
        assert sleep_durations == [pytest.approx(0.4)]
        assert clock.now - 1000.0 == pytest.approx(1.0)

    def test_a_probe_that_fails_at_the_deadline_is_not_followed_by_a_sleep(self, wait_script):
        clock = self._fake_clock()
        sleep_durations = []
        clock.sleep = sleep_durations.append

        def child_that_uses_all_the_time(command, **run_arguments):
            if command[2] == "import linuxcnc":
                return MagicMock(returncode=0, stdout="", stderr="")
            clock.now += run_arguments.get("timeout")
            return MagicMock(returncode=1, stdout="", stderr="error return without exception set")

        fake_subprocess = SimpleNamespace(
            run=child_that_uses_all_the_time, TimeoutExpired=subprocess.TimeoutExpired
        )
        with patch.object(wait_script, "subprocess", fake_subprocess), \
                patch.object(wait_script, "time", clock):
            became_ready = wait_script.wait_for_linuxcnc(timeout=1.0, interval=0.5)

        assert became_ready is False
        assert sleep_durations == []
