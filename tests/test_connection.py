"""
Unit tests for starting without LinuxCNC, connecting when it comes up, and health reporting.
"""

import contextlib
import importlib
import logging
import socket
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import grpc
import pytest


def wait_until(predicate, timeout=5.0):
    """Poll until predicate() is true; fail the test if it never is."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail("condition not reached in time")


class LinuxCNCSwitch:
    """
    Makes the mocked linuxcnc module behave as if LinuxCNC were running or not.

    While it is down, linuxcnc.stat() objects fail on poll(), as a newly
    created one does on a real machine. An object created while it was up
    keeps polling without error afterwards, which is also what the real
    module does. The fixtures route the servicer's reachability probe (a
    child process in production) to ``probe()``.

    ``in_process_attempts_while_down`` counts linuxcnc.stat() calls made
    while LinuxCNC is down. On a real machine a single one breaks the
    linuxcnc module for good, so the servicer must never make one.
    """

    def __init__(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        self.is_running = True
        self.checks_made = 0
        self.in_process_attempts_while_down = 0
        self._module = mock_linuxcnc_module
        self._template_stat = mock_linuxcnc_stat
        mock_linuxcnc_module.stat.side_effect = self._new_stat
        mock_linuxcnc_module.command.side_effect = lambda: MagicMock()
        mock_linuxcnc_module.error_channel.side_effect = self._new_error_channel

    def probe(self):
        """Stand-in for the child-process probe: whether LinuxCNC is running."""
        self.checks_made += 1
        return self.is_running

    def _new_stat(self):
        if self.is_running:
            return self._template_stat
        self.in_process_attempts_while_down += 1
        down_stat = MagicMock()
        down_stat.poll.side_effect = self._module.error("emcStatusBuffer invalid err=3")
        return down_stat

    @staticmethod
    def _new_error_channel():
        error_channel = MagicMock()
        error_channel.poll.return_value = None
        return error_channel


@pytest.fixture
def linuxcnc_switch(mock_linuxcnc_module, mock_linuxcnc_stat):
    return LinuxCNCSwitch(mock_linuxcnc_module, mock_linuxcnc_stat)


@pytest.fixture
def service_module(mock_linuxcnc_module, linuxcnc_switch):
    """The service module bound to the mocked linuxcnc, with fast retry timings."""
    from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

    with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
        sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
        linuxcnc_service = importlib.import_module("linuxcnc_grpc.linuxcnc_service")

        with patch.object(linuxcnc_service, "INITIAL_RECONNECT_INTERVAL", 0.01), \
                patch.object(linuxcnc_service, "MAX_WAIT_FOR_LINUXCNC_INTERVAL", 0.04), \
                patch.object(
                    linuxcnc_service.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
                    side_effect=linuxcnc_switch.probe,
                ):
            yield linuxcnc_service
    sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)


@pytest.fixture
def make_service(service_module):
    """Build servicers and stop their background threads afterwards."""
    services = []

    def _make_service(**servicer_arguments):
        service = service_module.LinuxCNCServiceServicer(**servicer_arguments)
        services.append(service)
        return service

    yield _make_service
    for service in services:
        service.close()


def _aborted_with(context):
    context.abort.assert_called_once()
    return context.abort.call_args[0][0]


class TestStartWithoutLinuxCNC:
    """Constructing the servicer while LinuxCNC is down."""

    def test_default_still_fails_fast(self, make_service, linuxcnc_switch):
        """Direct construction keeps the 1.0 behaviour: no LinuxCNC, no servicer."""
        linuxcnc_switch.is_running = False

        with pytest.raises(Exception, match="emcStatusBuffer invalid"):
            make_service()

    def test_starts_disconnected_when_linuxcnc_is_not_required(
        self, make_service, linuxcnc_switch
    ):
        linuxcnc_switch.is_running = False
        connection_changes = []

        service = make_service(
            require_linuxcnc=False, on_connection_change=connection_changes.append
        )

        assert service._connected is False
        assert connection_changes == []
        assert linuxcnc_switch.in_process_attempts_while_down == 0

    def test_reports_connected_when_linuxcnc_is_already_running(self, make_service):
        connection_changes = []

        service = make_service(
            require_linuxcnc=False, on_connection_change=connection_changes.append
        )

        assert service._connected is True
        assert connection_changes == [True]

    @pytest.mark.parametrize(
        "rpc_name, request_factory",
        [
            ("GetStatus", lambda pb: pb.GetStatusRequest()),
            ("SendCommand", lambda pb: pb.LinuxCNCCommand(
                state=pb.StateCommand(state=pb.STATE_ESTOP))),
            ("WaitComplete", lambda pb: pb.WaitCompleteRequest(timeout=0.1)),
            ("ResolveFile", lambda pb: pb.ResolveFileRequest(filename="part.ngc")),
            ("GetMachineConfig", lambda pb: pb.GetMachineConfigRequest()),
        ],
    )
    def test_unary_rpcs_are_unavailable_while_disconnected(
        self, make_service, linuxcnc_switch, mock_grpc_context, tmp_path,
        rpc_name, request_factory,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False, nc_files_dir=str(tmp_path))

        response = getattr(service, rpc_name)(request_factory(linuxcnc_pb2), mock_grpc_context)

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.UNAVAILABLE
        assert "LinuxCNC is not running" in mock_grpc_context.abort.call_args[0][1]

    @pytest.mark.parametrize(
        "rpc_name, request_factory",
        [
            ("StreamStatus", lambda pb: pb.StreamStatusRequest(interval_ms=10)),
            ("StreamErrors", lambda pb: pb.StreamErrorsRequest()),
        ],
    )
    def test_streams_are_unavailable_while_disconnected(
        self, make_service, linuxcnc_switch, mock_grpc_context, rpc_name, request_factory
    ):
        from linuxcnc_pb import linuxcnc_pb2

        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)

        messages = list(getattr(service, rpc_name)(
            request_factory(linuxcnc_pb2), mock_grpc_context
        ))

        assert messages == []
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.UNAVAILABLE

    def test_file_rpcs_work_without_linuxcnc(
        self, make_service, linuxcnc_switch, mock_grpc_context, tmp_path
    ):
        """Uploading, listing, reading and deleting files don't need LinuxCNC."""
        from linuxcnc_pb import linuxcnc_pb2

        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False, nc_files_dir=str(tmp_path))

        service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(filename="part.ngc", content="M2\n"),
            mock_grpc_context,
        )
        listing = service.ListFiles(linuxcnc_pb2.ListFilesRequest(), mock_grpc_context)
        read_back = service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
        )
        service.DeleteFile(
            linuxcnc_pb2.DeleteFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert [entry.name for entry in listing.files] == ["part.ngc"]
        assert read_back.content == b"M2\n"
        mock_grpc_context.abort.assert_not_called()


class TestWaitingForLinuxCNC:
    """The background thread that connects once LinuxCNC is up, and the RPC-side reconnect."""

    def test_connects_once_linuxcnc_comes_up(
        self, make_service, linuxcnc_switch, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        linuxcnc_switch.is_running = False
        connection_changes = []
        service = make_service(
            require_linuxcnc=False, on_connection_change=connection_changes.append
        )
        service.start_waiting_for_linuxcnc()
        # Several checks that find LinuxCNC down.
        wait_until(lambda: linuxcnc_switch.checks_made >= 3)
        assert connection_changes == []
        # Not one connection attempt from this process while it was down.
        assert linuxcnc_switch.in_process_attempts_while_down == 0

        linuxcnc_switch.is_running = True
        wait_until(lambda: connection_changes == [True])

        status = service.GetStatus(linuxcnc_pb2.GetStatusRequest(), mock_grpc_context)
        assert status.task.task_mode == linuxcnc_pb2.MODE_MANUAL
        mock_grpc_context.abort.assert_not_called()

    def test_thread_ends_once_connected_and_does_not_watch_afterwards(
        self, make_service, linuxcnc_switch
    ):
        """After connecting, nothing checks LinuxCNC again or changes the connection."""
        linuxcnc_switch.is_running = False
        connection_changes = []
        service = make_service(
            require_linuxcnc=False, on_connection_change=connection_changes.append
        )
        service.start_waiting_for_linuxcnc()
        waiting_thread = service._waiting_thread
        linuxcnc_switch.is_running = True
        waiting_thread.join(timeout=5.0)
        assert not waiting_thread.is_alive()
        generation_when_connected = service._connection_generation

        linuxcnc_switch.is_running = False
        service.start_waiting_for_linuxcnc()  # connected: starts nothing

        assert service._waiting_thread is waiting_thread
        assert connection_changes == [True]
        assert service._connected is True
        assert service._connection_generation == generation_when_connected

    def test_not_started_when_already_connected(self, make_service):
        service = make_service(require_linuxcnc=False)

        service.start_waiting_for_linuxcnc()

        assert service._waiting_thread is None

    def test_listener_is_called_with_the_lock_held(self, make_service, linuxcnc_switch):
        """The listener hears of the connection before any RPC can use it."""
        linuxcnc_switch.is_running = False
        lock_was_held = []
        service_holder = []

        def listener(connected):
            lock_was_held.append((connected, service_holder[0]._lock._is_owned()))

        service = make_service(require_linuxcnc=False, on_connection_change=listener)
        service_holder.append(service)
        service.start_waiting_for_linuxcnc()
        linuxcnc_switch.is_running = True
        wait_until(lambda: len(lock_was_held) >= 1)

        assert lock_was_held == [(True, True)]

    def test_check_overtaken_by_another_connect_does_not_connect_again(
        self, make_service, linuxcnc_switch, service_module
    ):
        """
        A check that started while disconnected must not replace a connection
        made while the check ran: that would drop the command a WaitComplete
        is waiting on and make it report a false RCS_DONE.
        """
        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)
        linuxcnc_switch.is_running = True
        command_in_progress = service_module.DispatchRecord(
            linuxcnc_serial=77, echo_serial_before_dispatch=None
        )

        def probe_during_which_something_else_connects():
            service._connect()
            service._latest_dispatch_record = command_in_progress
            return True

        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            side_effect=probe_during_which_something_else_connects,
        ):
            service.start_waiting_for_linuxcnc()
            service._waiting_thread.join(timeout=5.0)

        assert not service._waiting_thread.is_alive()
        assert service._connection_generation == 1
        assert service._latest_dispatch_record is command_in_progress

    def test_reconnect_does_not_attempt_while_unreachable(
        self, make_service, linuxcnc_switch, mock_linuxcnc_module
    ):
        """The RPC-side reconnect also asks the probe before touching the linuxcnc module."""
        service = make_service(require_linuxcnc=False)
        linuxcnc_switch.is_running = False

        with patch("linuxcnc_grpc.linuxcnc_service.time.sleep"):
            reconnected = service._reconnect()

        assert reconnected is False
        assert linuxcnc_switch.in_process_attempts_while_down == 0

    def test_close_during_a_check_prevents_a_late_connect(
        self, make_service, linuxcnc_switch, service_module
    ):
        """
        A check that finishes after close() has returned must not connect.

        close() waits only a limited time for the waiting thread, so it can
        return while a check is still running.
        """
        linuxcnc_switch.is_running = False
        connection_changes = []
        service = make_service(
            require_linuxcnc=False, on_connection_change=connection_changes.append
        )
        check_started = threading.Event()
        release_check = threading.Event()

        def slow_probe_that_finds_linuxcnc():
            check_started.set()
            release_check.wait(10.0)
            return True

        linuxcnc_switch.is_running = True
        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            side_effect=slow_probe_that_finds_linuxcnc,
        ):
            service.start_waiting_for_linuxcnc()
            waiting_thread = service._waiting_thread
            wait_for_waiting_thread = waiting_thread.join
            try:
                assert check_started.wait(5.0)
                # close() gives up on the blocked thread at once instead of
                # after its real two seconds.
                with patch.object(
                    waiting_thread, "join",
                    side_effect=lambda timeout=None: wait_for_waiting_thread(timeout=0.01),
                ):
                    closer = threading.Thread(target=service.close)
                    closer.start()
                    closer.join(timeout=5.0)
                close_had_returned = not closer.is_alive()
                check_was_still_running = waiting_thread.is_alive() and not release_check.is_set()
            finally:
                release_check.set()
            wait_for_waiting_thread(timeout=5.0)

        assert close_had_returned
        assert check_was_still_running
        assert not waiting_thread.is_alive()
        assert service._connected is False
        assert service._connection_generation == 0
        assert connection_changes == []

    def test_failed_connect_after_a_good_probe_is_survived(
        self, make_service, linuxcnc_switch, mock_linuxcnc_module
    ):
        """LinuxCNC vanishing between the probe and the connect doesn't end the wait."""
        linuxcnc_switch.is_running = False
        connection_changes = []
        service = make_service(
            require_linuxcnc=False, on_connection_change=connection_changes.append
        )
        connect_attempts = []
        real_connect = service._connect

        def connect_that_fails_once():
            connect_attempts.append(len(connect_attempts))
            if len(connect_attempts) == 1:
                raise mock_linuxcnc_module.error("emcStatusBuffer invalid err=3")
            real_connect()

        linuxcnc_switch.is_running = True
        with patch.object(service, "_connect", side_effect=connect_that_fails_once):
            service.start_waiting_for_linuxcnc()
            wait_until(lambda: connection_changes == [True])

        assert len(connect_attempts) == 2

    @staticmethod
    def _failing_connect(mock_linuxcnc_module, connect_attempts, real_connect=None,
                         failures_before_success=None, before_attempt=None):
        """A stand-in for _connect that fails, optionally only the first few times."""
        def connect():
            connect_attempts.append(len(connect_attempts) + 1)
            if before_attempt is not None:
                before_attempt(len(connect_attempts))
            succeeds = (
                failures_before_success is not None
                and len(connect_attempts) > failures_before_success
            )
            if succeeds:
                real_connect()
                return
            raise mock_linuxcnc_module.error("emcStatusBuffer invalid err=3")
        return connect

    def test_gives_up_after_a_bounded_number_of_failed_connects(
        self, make_service, linuxcnc_switch, mock_linuxcnc_module, service_module
    ):
        """
        A connect that fails after a good check leaves this process unable
        to connect, so the thread stops trying and reports it, once, without
        holding the servicer lock.
        """
        linuxcnc_switch.is_running = False
        connection_changes = []
        reports = []
        service_holder = []

        def on_unrecoverable(error_text):
            service = service_holder[0]
            lock_taken_by_another_thread = []

            def take_the_lock():
                lock_was_free = service._lock.acquire(timeout=5.0)
                if lock_was_free:
                    service._lock.release()
                lock_taken_by_another_thread.append(lock_was_free)

            other_thread = threading.Thread(target=take_the_lock)
            other_thread.start()
            other_thread.join(timeout=10.0)
            reports.append(
                (error_text, service._lock._is_owned(), lock_taken_by_another_thread)
            )

        service = make_service(
            require_linuxcnc=False,
            on_connection_change=connection_changes.append,
            on_unrecoverable=on_unrecoverable,
        )
        service_holder.append(service)
        connect_attempts = []

        linuxcnc_switch.is_running = True
        with patch.object(
            service, "_connect",
            side_effect=self._failing_connect(mock_linuxcnc_module, connect_attempts),
        ):
            service.start_waiting_for_linuxcnc()
            service._waiting_thread.join(timeout=5.0)

        assert not service._waiting_thread.is_alive()
        assert len(connect_attempts) == service_module.MAX_CONNECT_ATTEMPTS_AFTER_GOOD_PROBE
        assert len(reports) == 1
        error_text, lock_owned_by_the_reporting_thread, lock_taken_by_another_thread = reports[0]
        assert "emcStatusBuffer invalid" in error_text
        assert lock_owned_by_the_reporting_thread is False
        assert lock_taken_by_another_thread == [True]
        assert service._connected is False
        assert connection_changes == []

    def test_report_is_dropped_if_close_was_called_and_comes_before_the_log(
        self, make_service, linuxcnc_switch, caplog
    ):
        """
        Nothing is done between reading the stop flag and calling the
        listener, so close() has the least possible time to slip in between.
        """
        linuxcnc_switch.is_running = False
        errors_logged_when_the_listener_ran = []
        reports = []

        def errors_logged():
            return [record for record in caplog.records if record.levelno == logging.ERROR]

        def on_unrecoverable(error_text):
            errors_logged_when_the_listener_ran.append(len(errors_logged()))
            reports.append(error_text)

        service = make_service(require_linuxcnc=False, on_unrecoverable=on_unrecoverable)

        with caplog.at_level(logging.ERROR, logger="linuxcnc_grpc.linuxcnc_service"):
            service._report_unrecoverable("first")
            errors_after_the_first_report = len(errors_logged())
            service.close()
            service._report_unrecoverable("second")

        assert reports == ["first"]
        assert errors_logged_when_the_listener_ran == [0]
        assert errors_after_the_first_report == 1
        assert len(errors_logged()) == 1

    def test_giving_up_without_a_listener_logs_an_error(
        self, make_service, linuxcnc_switch, mock_linuxcnc_module, caplog
    ):
        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)

        linuxcnc_switch.is_running = True
        with caplog.at_level(logging.ERROR, logger="linuxcnc_grpc.linuxcnc_service"), \
                patch.object(
                    service, "_connect",
                    side_effect=self._failing_connect(mock_linuxcnc_module, []),
                ):
            service.start_waiting_for_linuxcnc()
            service._waiting_thread.join(timeout=5.0)

        assert not service._waiting_thread.is_alive()
        error_messages = [
            record.getMessage() for record in caplog.records if record.levelno == logging.ERROR
        ]
        assert len(error_messages) == 1
        assert "can no longer connect to LinuxCNC" in error_messages[0]

    def test_connect_that_succeeds_within_the_bound_reports_nothing(
        self, make_service, linuxcnc_switch, mock_linuxcnc_module, service_module
    ):
        linuxcnc_switch.is_running = False
        connection_changes = []
        reports = []
        service = make_service(
            require_linuxcnc=False,
            on_connection_change=connection_changes.append,
            on_unrecoverable=reports.append,
        )
        connect_attempts = []
        failures_before_success = service_module.MAX_CONNECT_ATTEMPTS_AFTER_GOOD_PROBE - 1

        linuxcnc_switch.is_running = True
        with patch.object(
            service, "_connect",
            side_effect=self._failing_connect(
                mock_linuxcnc_module, connect_attempts, real_connect=service._connect,
                failures_before_success=failures_before_success,
            ),
        ):
            service.start_waiting_for_linuxcnc()
            service._waiting_thread.join(timeout=5.0)

        assert not service._waiting_thread.is_alive()
        assert len(connect_attempts) == failures_before_success + 1
        assert connection_changes == [True]
        assert reports == []

    def test_close_during_the_retries_prevents_the_report(
        self, make_service, linuxcnc_switch, mock_linuxcnc_module, service_module
    ):
        """A server that is being stopped does not also ask to be restarted."""
        linuxcnc_switch.is_running = False
        reports = []
        service = make_service(require_linuxcnc=False, on_unrecoverable=reports.append)
        connect_attempts = []
        closer = threading.Thread(target=service.close)

        def close_during_the_last_attempt(attempt_number):
            if attempt_number == service_module.MAX_CONNECT_ATTEMPTS_AFTER_GOOD_PROBE:
                # close() is called from another thread while this attempt
                # holds the lock; the attempt fails once close() has begun.
                closer.start()
                assert service._stop_requested.wait(5.0)

        linuxcnc_switch.is_running = True
        with patch.object(
            service, "_connect",
            side_effect=self._failing_connect(
                mock_linuxcnc_module, connect_attempts,
                before_attempt=close_during_the_last_attempt,
            ),
        ):
            service.start_waiting_for_linuxcnc()
            waiting_thread = service._waiting_thread
            waiting_thread.join(timeout=5.0)
            closer.join(timeout=5.0)

        assert not waiting_thread.is_alive()
        assert not closer.is_alive()
        assert len(connect_attempts) == service_module.MAX_CONNECT_ATTEMPTS_AFTER_GOOD_PROBE
        assert reports == []

    def test_close_is_bounded_while_the_lock_is_held(
        self, make_service, linuxcnc_switch, service_module
    ):
        """
        close() returns although another thread holds the servicer lock (as
        a command LinuxCNC is not answering does), and still stops any later
        connect.
        """
        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)
        linuxcnc_switch.is_running = True
        snapshot_before_close = service._connection_snapshot()
        lock_is_held = threading.Event()
        release_lock = threading.Event()

        def hold_the_lock():
            with service._lock:
                lock_is_held.set()
                release_lock.wait(timeout=10.0)

        # The lock is re-entrant, so holder and closer must be different threads.
        lock_holder = threading.Thread(target=hold_the_lock)
        closer = threading.Thread(target=service.close)
        lock_holder.start()
        try:
            assert lock_is_held.wait(timeout=5.0)
            with patch.object(service_module, "CLOSE_LOCK_TIMEOUT", 0.01):
                closer.start()
                closer.join(timeout=5.0)
            closed_while_the_lock_was_held = not closer.is_alive()
        finally:
            release_lock.set()
            lock_holder.join(timeout=5.0)
            closer.join(timeout=5.0)

        assert closed_while_the_lock_was_held
        assert service._connect_if_unchanged_since(snapshot_before_close) is False
        assert service._connected is False
        # A reader that was waiting for the lock takes no message afterwards.
        service._connected = True
        service._error_channel = MagicMock()
        assert service._poll_error_channel() is None
        service._error_channel.poll.assert_not_called()
        # Closing again, with the lock free, is harmless.
        service.close()

    def test_nothing_connects_once_close_has_returned(self, make_service, linuxcnc_switch):
        """After close(), neither the waiting thread's nor an RPC's connect path publishes channels."""
        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)
        linuxcnc_switch.is_running = True
        snapshot_before_close = service._connection_snapshot()

        service.close()

        assert service._connect_if_unchanged_since(snapshot_before_close) is False
        with patch("linuxcnc_grpc.linuxcnc_service.time.sleep"):
            assert service._reconnect() is False
        assert service._connected is False
        assert service._connection_generation == 0

    def test_rpc_reconnect_leaves_a_connection_made_meanwhile_alone(
        self, make_service, linuxcnc_switch, service_module
    ):
        """_reconnect() does not replace a connection another RPC made during its check."""
        service = make_service(require_linuxcnc=False)
        generation_before = service._connection_generation

        def probe_during_which_another_rpc_reconnects():
            service._connect()
            return True

        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            side_effect=probe_during_which_another_rpc_reconnects,
        ), patch("linuxcnc_grpc.linuxcnc_service.time.sleep"):
            reconnected = service._reconnect()

        assert reconnected is True
        assert service._connection_generation == generation_before + 1

    def test_rpc_reconnect_for_an_old_failure_leaves_a_newer_connection_alone(
        self, make_service, service_module
    ):
        """A poll that failed on a connection since replaced does not replace the new one."""
        service = make_service(require_linuxcnc=False)
        connection_that_failed = service._connection_snapshot()
        service._connect()  # another RPC reconnected first, then dispatched
        command_in_progress = service_module.DispatchRecord(
            linuxcnc_serial=5, echo_serial_before_dispatch=None
        )
        service._latest_dispatch_record = command_in_progress
        generation_after_other_reconnect = service._connection_generation

        with patch("linuxcnc_grpc.linuxcnc_service.time.sleep"):
            reconnected = service._reconnect(connection_that_failed)

        assert reconnected is True
        assert service._connection_generation == generation_after_other_reconnect
        assert service._latest_dispatch_record is command_in_progress

    def test_get_status_reconnects_for_the_connection_it_polled(
        self, make_service, mock_linuxcnc_module
    ):
        """GetStatus tells _reconnect which connection failed, from the failing lock hold."""
        service = make_service(require_linuxcnc=False)
        connection_polled = service._connection_snapshot()
        service._stat.poll = MagicMock(side_effect=mock_linuxcnc_module.error("poll failed"))

        with patch.object(service, "_reconnect", return_value=False) as reconnect:
            service.GetStatus(MagicMock(), MagicMock())

        reconnect.assert_called_once_with(connection_polled)

    def test_thread_that_cannot_start_is_not_kept(self, make_service, linuxcnc_switch):
        """If the thread can't be started, close() still works and a later start can retry."""
        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)

        with patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start")):
            with pytest.raises(RuntimeError, match="can't start"):
                service.start_waiting_for_linuxcnc()

        assert service._waiting_thread is None
        service.close()

    def test_start_is_idempotent_and_close_stops_the_thread(
        self, make_service, linuxcnc_switch
    ):
        linuxcnc_switch.is_running = False
        service = make_service(require_linuxcnc=False)
        service.start_waiting_for_linuxcnc()
        waiting_thread = service._waiting_thread
        service.start_waiting_for_linuxcnc()
        assert service._waiting_thread is waiting_thread

        service.close()
        waiting_thread.join(timeout=5.0)

        assert not waiting_thread.is_alive()

    def test_listener_exception_does_not_break_the_servicer(self, make_service):
        def failing_listener(connected):
            raise RuntimeError("listener bug")

        service = make_service(require_linuxcnc=False, on_connection_change=failing_listener)

        assert service._connected is True


class TestServerHealthAndOptions:
    """create_server wiring: health follows the connection, flags reach the servicer."""

    @pytest.fixture
    def server_module(self, mock_linuxcnc_module, linuxcnc_switch):
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module, "hal": MagicMock()}):
            for module_name in ("linuxcnc_grpc.server", "linuxcnc_grpc.linuxcnc_service",
                                "linuxcnc_grpc.hal_service"):
                sys.modules.pop(module_name, None)
            server_module = importlib.import_module("linuxcnc_grpc.server")
            linuxcnc_service = importlib.import_module("linuxcnc_grpc.linuxcnc_service")

            with patch.object(linuxcnc_service, "INITIAL_RECONNECT_INTERVAL", 0.01), \
                    patch.object(linuxcnc_service, "MAX_WAIT_FOR_LINUXCNC_INTERVAL", 0.04), \
                    patch.object(
                        linuxcnc_service.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
                        side_effect=linuxcnc_switch.probe,
                    ), \
                    patch.object(server_module, "HalServiceServicer"), \
                    patch.object(server_module.hal_pb2_grpc, "add_HalServiceServicer_to_server"):
                yield server_module
        for module_name in ("linuxcnc_grpc.server", "linuxcnc_grpc.linuxcnc_service",
                            "linuxcnc_grpc.hal_service"):
            sys.modules.pop(module_name, None)

    @staticmethod
    def _health_status(channel, service_name):
        from grpc_health.v1 import health_pb2, health_pb2_grpc

        return health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(service=service_name), timeout=5.0
        ).status

    @staticmethod
    @contextlib.contextmanager
    def _recording_transport(server_module, events):
        """
        Let create_server build real gRPC servers, recording on each one the
        port it bound and every call of its own start() and stop() (the stop
        create_server wraps). Yields the list of servers built, which also
        keeps them alive.
        """
        real_grpc_server = server_module.grpc.server
        servers_built = []

        def recording_grpc_server(*arguments, **keyword_arguments):
            grpc_server = real_grpc_server(*arguments, **keyword_arguments)
            real_add_insecure_port = grpc_server.add_insecure_port
            real_start = grpc_server.start
            real_stop = grpc_server.stop
            grpc_server.bound_ports = []

            def recording_start():
                events.append("transport start")
                real_start()

            def recording_add_insecure_port(address):
                bound_port = real_add_insecure_port(address)
                grpc_server.bound_ports.append(bound_port)
                return bound_port

            def recording_stop(grace):
                events.append(("transport stop", grace))
                return real_stop(grace)

            grpc_server.add_insecure_port = recording_add_insecure_port
            grpc_server.start = recording_start
            grpc_server.stop = recording_stop
            servers_built.append(grpc_server)
            return grpc_server

        with patch.object(server_module.grpc, "server", recording_grpc_server):
            yield servers_built

    @staticmethod
    @contextlib.contextmanager
    def _recording_servicers(server_module):
        """Yield the list of LinuxCNC servicers create_server builds."""
        real_servicer_class = server_module.LinuxCNCServiceServicer
        servicers_built = []

        def recording_servicer(**servicer_arguments):
            servicer = real_servicer_class(**servicer_arguments)
            servicers_built.append(servicer)
            return servicer

        with patch.object(server_module, "LinuxCNCServiceServicer", recording_servicer):
            yield servicers_built

    @pytest.fixture
    def make_server(self, server_module):
        """
        create_server, with every server it returns stopped after the test,
        whatever the test asserted, so no servicer thread outlives it.
        """
        servers = []

        def _make_server(**server_arguments):
            server_arguments.setdefault("host", "127.0.0.1")
            server_arguments.setdefault("port", 0)
            server = server_module.create_server(**server_arguments)
            servers.append(server)
            return server

        yield _make_server
        for server in servers:
            server.stop(grace=0)

    @staticmethod
    def _unused_port():
        """A port number that was free a moment ago."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            return probe_socket.getsockname()[1]

    @staticmethod
    def _port_can_be_bound(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            try:
                probe_socket.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    @staticmethod
    def _servicer_threads():
        return {
            thread for thread in threading.enumerate()
            if thread.name in ("linuxcnc-error-reader", "linuxcnc-connection-waiter")
            and thread.is_alive()
        }

    def test_health_follows_the_linuxcnc_connection(self, make_server, linuxcnc_switch):
        """NOT_SERVING until connected, SERVING after, over a real gRPC connection."""
        from grpc_health.v1 import health_pb2
        from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

        linuxcnc_switch.is_running = False
        server = make_server()
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                not_serving = health_pb2.HealthCheckResponse.NOT_SERVING
                serving = health_pb2.HealthCheckResponse.SERVING
                assert self._health_status(channel, "") == not_serving
                assert self._health_status(channel, "linuxcnc.LinuxCNCService") == not_serving
                assert self._health_status(channel, "hal.HalService") == serving

                stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)
                with pytest.raises(grpc.RpcError) as error_info:
                    stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=5.0)
                assert error_info.value.code() == grpc.StatusCode.UNAVAILABLE

                linuxcnc_switch.is_running = True
                wait_until(lambda: self._health_status(channel, "") == serving)
                assert self._health_status(channel, "linuxcnc.LinuxCNCService") == serving
                status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=5.0)
                assert len(status.joints) == 3
        finally:
            server.stop(grace=0)

    def test_require_linuxcnc_fails_at_startup(self, server_module, linuxcnc_switch):
        linuxcnc_switch.is_running = False

        with pytest.raises(Exception, match="emcStatusBuffer invalid"):
            server_module.create_server(host="127.0.0.1", port=0, require_linuxcnc=True)

    def test_stopping_the_server_stops_the_servicer_threads(
        self, make_server, linuxcnc_switch
    ):
        """A stopped server must not keep a thread reading LinuxCNC's error channel."""
        linuxcnc_switch.is_running = False
        threads_before = self._servicer_threads()
        server = make_server(error_channel_mode="always")
        server.start()
        servicer_threads = self._servicer_threads() - threads_before
        assert len(servicer_threads) == 2

        server.stop(grace=0)

        for servicer_thread in servicer_threads:
            servicer_thread.join(timeout=5.0)
            assert not servicer_thread.is_alive()

    @pytest.mark.parametrize("failing_step", [
        "linuxcnc servicer", "hal servicer", "registration interrupted",
        "waiting thread", "bind",
    ])
    def test_failed_create_server_binds_no_port_starts_nothing_and_leaves_no_thread(
        self, server_module, linuxcnc_switch, failing_step
    ):
        """
        The port is bound last and nothing is ever started by create_server,
        so no failure can leave a port taken or a half-built server serving.
        """
        threads_before = self._servicer_threads()
        events = []
        servicer_class = sys.modules["linuxcnc_grpc.linuxcnc_service"].LinuxCNCServiceServicer
        port = self._unused_port()
        server_arguments = {"host": "127.0.0.1", "port": port, "error_channel_mode": "always"}
        expected_exception = Exception

        with contextlib.ExitStack() as patches:
            servers_built = patches.enter_context(
                self._recording_transport(server_module, events)
            )
            servicers_built = patches.enter_context(self._recording_servicers(server_module))
            if failing_step == "linuxcnc servicer":
                linuxcnc_switch.is_running = False
                server_arguments["require_linuxcnc"] = True
                expected_error = "emcStatusBuffer invalid"
            elif failing_step == "hal servicer":
                patches.enter_context(patch.object(
                    server_module, "HalServiceServicer",
                    side_effect=RuntimeError("Failed to access HAL"),
                ))
                expected_error = "Failed to access HAL"
            elif failing_step == "registration interrupted":
                # Ctrl+C after the LinuxCNC service was registered.
                patches.enter_context(patch.object(
                    server_module.hal_pb2_grpc, "add_HalServiceServicer_to_server",
                    side_effect=KeyboardInterrupt("interrupted"),
                ))
                expected_exception = KeyboardInterrupt
                expected_error = "interrupted"
            elif failing_step == "waiting thread":
                linuxcnc_switch.is_running = False
                patches.enter_context(patch.object(
                    servicer_class, "start_waiting_for_linuxcnc",
                    side_effect=RuntimeError("can't start new thread"),
                ))
                expected_error = "can't start new thread"
            else:
                server_arguments["host"] = "256.256.256.256"
                expected_error = None

            with pytest.raises(expected_exception, match=expected_error):
                server_module.create_server(**server_arguments)

        # The server object is still referenced (servers_built), so a free
        # port is not the work of garbage collection.
        (failed_server,) = servers_built
        assert failed_server.bound_ports == []
        assert "transport start" not in events
        for servicer in servicers_built:
            assert servicer._stop_requested.is_set()
        wait_until(lambda: self._port_can_be_bound(port))
        wait_until(lambda: self._servicer_threads() <= threads_before)

    def test_create_server_binds_the_port_after_everything_else(self, server_module):
        """Nothing that can fail comes after the bind."""
        steps = []
        servicer_class = sys.modules["linuxcnc_grpc.linuxcnc_service"].LinuxCNCServiceServicer
        real_grpc_server = server_module.grpc.server
        real_start_waiting = servicer_class.start_waiting_for_linuxcnc
        servers = []

        def grpc_server_recording_the_bind(*arguments, **keyword_arguments):
            grpc_server = real_grpc_server(*arguments, **keyword_arguments)
            real_add_insecure_port = grpc_server.add_insecure_port

            def recording_add_insecure_port(address):
                steps.append("bind")
                return real_add_insecure_port(address)

            grpc_server.add_insecure_port = recording_add_insecure_port
            return grpc_server

        def recording_start_waiting(servicer):
            steps.append("waiting thread")
            real_start_waiting(servicer)

        with patch.object(server_module.grpc, "server", grpc_server_recording_the_bind), \
                patch.object(
                    server_module.linuxcnc_pb2_grpc, "add_LinuxCNCServiceServicer_to_server",
                    side_effect=lambda servicer, server: steps.append("linuxcnc registered"),
                ), patch.object(
                    server_module.hal_pb2_grpc, "add_HalServiceServicer_to_server",
                    side_effect=lambda servicer, server: steps.append("hal registered"),
                ), patch.object(
                    servicer_class, "start_waiting_for_linuxcnc", recording_start_waiting
                ):
            try:
                servers.append(server_module.create_server(host="127.0.0.1", port=0))
            finally:
                for server in servers:
                    server.stop(grace=0)

        assert steps == ["linuxcnc registered", "hal registered", "waiting thread", "bind"]

    def test_stop_does_not_wait_for_the_servicer_lock(self, server_module, make_server):
        """
        A command that LinuxCNC is not answering holds the servicer lock for
        seconds. Stopping the server must start the transport shutdown at
        once and return; closing the servicer finishes when the lock is free.
        """
        events = []
        lock_is_held = threading.Event()
        release_lock = threading.Event()
        stop_results = []

        with self._recording_transport(server_module, events), \
                self._recording_servicers(server_module) as servicers_built:
            server = make_server()
        (servicer,) = servicers_built
        server.start()
        error_reader = servicer._error_broker._reader_thread

        def hold_the_lock():
            with servicer._lock:
                lock_is_held.set()
                release_lock.wait(timeout=10.0)

        # The lock is re-entrant, so holder and stopper must be different threads.
        lock_holder = threading.Thread(target=hold_the_lock)
        stopper = threading.Thread(target=lambda: stop_results.append(server.stop(0)))
        lock_holder.start()
        try:
            assert lock_is_held.wait(timeout=5.0)
            with patch.object(
                sys.modules["linuxcnc_grpc.linuxcnc_service"], "CLOSE_LOCK_TIMEOUT", 0.01
            ):
                stopper.start()
                stopper.join(timeout=3.0)
            stop_returned_while_the_lock_was_held = not stopper.is_alive()
            events_while_the_lock_was_held = list(events)
        finally:
            release_lock.set()
            lock_holder.join(timeout=5.0)
            stopper.join(timeout=10.0)

        assert stop_returned_while_the_lock_was_held
        assert events_while_the_lock_was_held == ["transport start", ("transport stop", 0)]
        assert stop_results[0].wait(timeout=5.0)
        # With the lock free again, closing completes and the threads are gone.
        servicer.close()
        error_reader.join(timeout=5.0)
        assert not error_reader.is_alive()

    @pytest.mark.parametrize("failing_step", ["health", "transport stop"])
    def test_stop_still_closes_the_servicer_when_an_earlier_step_raises(
        self, server_module, make_server, failing_step
    ):
        """Each step of stopping runs even if the one before it raised."""
        events = []
        with self._recording_transport(server_module, events) as servers_built, \
                self._recording_servicers(server_module) as servicers_built:
            server = make_server()
        (servicer,) = servicers_built
        (grpc_server,) = servers_built
        server.start()

        if failing_step == "health":
            failing_patch = patch.object(
                server_module.health.HealthServicer, "enter_graceful_shutdown",
                side_effect=RuntimeError("health failed"),
            )
            expected_error = "health failed"
        else:
            # create_server kept the server's own stop; fail on its first call only.
            real_transport_stop = server_module.grpc._server._stop
            transport_stop_calls = []

            def transport_stop_failing_once(state, grace):
                transport_stop_calls.append(grace)
                if len(transport_stop_calls) == 1:
                    raise RuntimeError("transport failed")
                return real_transport_stop(state, grace)

            failing_patch = patch.object(
                server_module.grpc._server, "_stop", transport_stop_failing_once
            )
            expected_error = "transport failed"

        with failing_patch:
            with pytest.raises(RuntimeError, match=expected_error):
                server.stop(grace=0)
            events_of_the_failed_stop = list(events)
            servicer_was_closed = servicer._stop_requested.is_set()

        assert ("transport stop", 0) in events_of_the_failed_stop
        assert servicer_was_closed

    def test_unrecoverable_connection_reaches_the_callback_given_to_create_server(
        self, make_server, linuxcnc_switch, mock_linuxcnc_module
    ):
        linuxcnc_switch.is_running = False
        reports = []
        servicer_class = sys.modules["linuxcnc_grpc.linuxcnc_service"].LinuxCNCServiceServicer

        with patch.object(
            servicer_class, "_connect",
            side_effect=mock_linuxcnc_module.error("emcStatusBuffer invalid err=3"),
        ):
            server = make_server(on_unrecoverable=reports.append)
            server.start()
            linuxcnc_switch.is_running = True
            wait_until(lambda: len(reports) == 1)
            server.stop(grace=0)

        assert "emcStatusBuffer invalid" in reports[0]

    def test_signal_handler_only_records_the_request(self, server_module):
        """
        The handler does not log, stop the server or block; serve() stops it afterwards.

        A handler runs in the main thread in the middle of whatever it was
        doing, so stopping the server, logging or setting a threading.Event
        there can deadlock against a lock the interrupted code holds.

        Shown for the logging case: the handler is called while this test
        holds the lock of a log handler on the server's logger, as if the
        signal had interrupted a logging call. A log call made under the
        same conditions is the control that the held lock really blocks.
        """
        fake_server = MagicMock()
        threads_that_stopped_the_server = []
        fake_server.stop.side_effect = lambda grace: (
            threads_that_stopped_the_server.append(threading.current_thread()) or MagicMock()
        )
        installed_handlers = {}
        messages_logged = []

        class RecordingLogHandler(logging.Handler):
            def emit(self, record):
                messages_logged.append(record.getMessage())

        held_log_handler = RecordingLogHandler()
        server_logger = server_module.logger
        level_before = server_logger.level

        with patch.object(server_module, "create_server", return_value=fake_server), \
                patch.object(server_module, "SHUTDOWN_POLL_INTERVAL", 0.01), \
                patch.object(
                    server_module.signal, "signal",
                    side_effect=lambda signal_number, handler: installed_handlers.__setitem__(
                        signal_number, handler
                    ),
                ):
            serving = threading.Thread(target=server_module.serve, kwargs={"port": 0})
            serving.start()
            wait_until(lambda: fake_server.start.called and len(installed_handlers) == 2)
            shutdown_handler = installed_handlers[server_module.signal.SIGTERM]

            handler_caller = threading.Thread(
                target=shutdown_handler, args=(server_module.signal.SIGTERM, None), daemon=True
            )
            logging_caller = threading.Thread(
                target=server_logger.info, args=("control",), daemon=True
            )
            # Held before it is attached, so nothing gets through it early.
            held_log_handler.acquire()
            try:
                server_logger.addHandler(held_log_handler)
                server_logger.setLevel(logging.DEBUG)
                # Logging locks are reentrant, so the callers are other threads.
                logging_caller.start()
                handler_caller.start()
                handler_caller.join(timeout=5.0)
                handler_returned_while_lock_was_held = not handler_caller.is_alive()
                # The control: a log call cannot get past the held lock.
                logging_caller.join(timeout=0.1)
                control_was_blocked = logging_caller.is_alive()
                messages_logged_while_lock_was_held = list(messages_logged)
            finally:
                held_log_handler.release()
                logging_caller.join(timeout=5.0)
                serving.join(timeout=5.0)
                server_logger.removeHandler(held_log_handler)
                server_logger.setLevel(level_before)

        assert handler_returned_while_lock_was_held
        assert control_was_blocked
        assert messages_logged_while_lock_was_held == []
        assert "control" in messages_logged
        assert not serving.is_alive()
        # Stopped once, by serve() itself and not from inside the handler.
        assert threads_that_stopped_the_server == [serving]
        fake_server.stop.assert_called_once_with(grace=5)

    def _serve_with_recognisable_previous_handlers(self, server_module, fake_server):
        """Run serve() in this (the main) thread; return the handlers left installed."""
        real_signal = server_module.signal

        def previous_interrupt_handler(signal_number, frame):
            pass

        def previous_terminate_handler(signal_number, frame):
            pass

        previous_handlers = {
            real_signal.SIGINT: previous_interrupt_handler,
            real_signal.SIGTERM: previous_terminate_handler,
        }
        handlers_before_the_test = {
            signal_number: real_signal.signal(signal_number, previous_handler)
            for signal_number, previous_handler in previous_handlers.items()
        }
        try:
            with patch.object(server_module, "create_server", return_value=fake_server), \
                    patch.object(server_module, "SHUTDOWN_POLL_INTERVAL", 0.01):
                try:
                    server_module.serve(port=0)
                except RuntimeError:
                    pass
            handlers_after_serve = {
                signal_number: real_signal.getsignal(signal_number)
                for signal_number in previous_handlers
            }
        finally:
            for signal_number, handler_before_the_test in handlers_before_the_test.items():
                real_signal.signal(signal_number, handler_before_the_test)
        return previous_handlers, handlers_after_serve

    def test_serve_restores_the_previous_signal_handlers_when_it_returns(self, server_module):
        """A signal after serve() has returned must not go to the finished call's handler."""
        real_signal = server_module.signal
        fake_server = MagicMock()
        # "Starting" the server delivers the shutdown request to serve()'s own handler.
        fake_server.start.side_effect = lambda: real_signal.getsignal(real_signal.SIGTERM)(
            real_signal.SIGTERM, None
        )

        previous_handlers, handlers_after_serve = self._serve_with_recognisable_previous_handlers(
            server_module, fake_server
        )

        fake_server.stop.assert_called_once_with(grace=5)
        assert handlers_after_serve == previous_handlers

    def test_serve_restores_the_previous_signal_handlers_when_it_fails(self, server_module):
        fake_server = MagicMock()
        fake_server.start.side_effect = RuntimeError("cannot start")

        previous_handlers, handlers_after_serve = self._serve_with_recognisable_previous_handlers(
            server_module, fake_server
        )

        fake_server.stop.assert_called_once_with(grace=5)
        assert handlers_after_serve == previous_handlers

    def test_serve_restores_the_signal_handlers_even_if_stopping_fails(self, server_module):
        fake_server = MagicMock()
        fake_server.start.side_effect = RuntimeError("cannot start")
        fake_server.stop.side_effect = RuntimeError("cannot stop")

        previous_handlers, handlers_after_serve = self._serve_with_recognisable_previous_handlers(
            server_module, fake_server
        )

        assert handlers_after_serve == previous_handlers

    def test_serve_stops_the_server_if_starting_it_fails(self, server_module):
        """A failure after create_server still stops the server and its servicer threads."""
        fake_server = MagicMock()
        fake_server.start.side_effect = RuntimeError("cannot start")

        with patch.object(server_module, "create_server", return_value=fake_server), \
                patch.object(server_module.signal, "signal"):
            with pytest.raises(RuntimeError, match="cannot start"):
                server_module.serve(port=0)

        fake_server.stop.assert_called_once_with(grace=5)

    @pytest.mark.parametrize("start_fails", [False, True])
    def test_serve_does_not_wait_for_ever_for_a_server_that_never_finishes_stopping(
        self, server_module, caplog, start_fails
    ):
        """Also when starting failed, which goes through the same cleanup."""
        real_signal = server_module.signal
        fake_server = MagicMock()
        never_stopped = threading.Event()
        waits = []

        def wait_recording_the_timeout(timeout=None):
            waits.append(timeout)
            return never_stopped.is_set()

        fake_server.stop.return_value.wait.side_effect = wait_recording_the_timeout
        installed_handlers = {}
        if start_fails:
            fake_server.start.side_effect = RuntimeError("cannot start")
        else:
            fake_server.start.side_effect = lambda: installed_handlers[real_signal.SIGTERM](
                real_signal.SIGTERM, None
            )

        with patch.object(server_module, "create_server", return_value=fake_server), \
                patch.object(server_module, "SHUTDOWN_POLL_INTERVAL", 0.01), \
                patch.object(
                    real_signal, "signal",
                    side_effect=lambda signal_number, handler: installed_handlers.__setitem__(
                        signal_number, handler
                    ),
                ), caplog.at_level(logging.ERROR, logger=server_module.logger.name):
            if start_fails:
                with pytest.raises(RuntimeError, match="cannot start"):
                    server_module.serve(port=0)
            else:
                server_module.serve(port=0)

        fake_server.stop.assert_called_once_with(grace=server_module.SHUTDOWN_GRACE_PERIOD)
        assert waits == [server_module.SHUTDOWN_WAIT_TIMEOUT]
        assert server_module.SHUTDOWN_WAIT_TIMEOUT > server_module.SHUTDOWN_GRACE_PERIOD
        assert "did not finish stopping" in caplog.text

    def test_stop_sets_health_then_stops_the_transport_then_closes_the_servicer(
        self, server_module, make_server
    ):
        """The order of the three steps of stopping, as recorded server-side."""
        from grpc_health.v1 import health_pb2

        events = []
        with self._recording_transport(server_module, events):
            server = make_server()
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        servicer_class = sys.modules["linuxcnc_grpc.linuxcnc_service"].LinuxCNCServiceServicer
        real_close = servicer_class.close

        def recording_close(servicer):
            events.append("servicer closed")
            # The real one too, or this server's error reader would run on.
            real_close(servicer)

        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            assert self._health_status(channel, "") == health_pb2.HealthCheckResponse.SERVING
            real_health_set = server_module.health.HealthServicer.set

            def recording_set(health_servicer, service_name, serving_status):
                events.append((service_name, serving_status))
                real_health_set(health_servicer, service_name, serving_status)

            with patch.object(server_module.health.HealthServicer, "set", recording_set), \
                    patch.object(servicer_class, "close", recording_close):
                server.stop(grace=0)

        health_turned_not_serving = events.index(("", health_pb2.HealthCheckResponse.NOT_SERVING))
        transport_stopped = events.index(("transport stop", 0))
        servicer_closed = events.index("servicer closed")
        assert health_turned_not_serving < transport_stopped < servicer_closed

    def test_health_watcher_sees_not_serving_when_the_server_stops(self, make_server):
        """What a client watching the health service gets before the transport goes away."""
        from grpc_health.v1 import health_pb2, health_pb2_grpc

        server = make_server()
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        stopped = None
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                health_updates = health_pb2_grpc.HealthStub(channel).Watch(
                    health_pb2.HealthCheckRequest(service=""), timeout=10.0
                )
                assert next(health_updates).status == health_pb2.HealthCheckResponse.SERVING

                # The grace period keeps the watch open while the update is delivered.
                stopped = server.stop(grace=5)

                assert next(health_updates).status == health_pb2.HealthCheckResponse.NOT_SERVING
                health_updates.cancel()
        finally:
            if stopped is None:
                stopped = server.stop(grace=0)
            assert stopped.wait(timeout=10.0)

    def test_serve_restores_the_default_action_for_a_handler_not_set_from_python(
        self, server_module
    ):
        """
        signal.signal() returns None for a handler that was not installed
        from Python. serve() cannot put that back, and must not leave its
        own handler, so it restores the default action.
        """
        real_signal = server_module.signal
        fake_server = MagicMock()
        signal_calls = []

        def signal_reporting_no_python_handler(signal_number, handler):
            signal_calls.append((signal_number, handler))
            if len(signal_calls) == 2:
                # Both of serve()'s handlers are in place: ask it to stop.
                handler(real_signal.SIGTERM, None)
            return None

        with patch.object(server_module, "create_server", return_value=fake_server), \
                patch.object(server_module, "SHUTDOWN_POLL_INTERVAL", 0.01), \
                patch.object(
                    real_signal, "signal", side_effect=signal_reporting_no_python_handler
                ):
            server_module.serve(port=0)

        assert signal_calls[2:] == [
            (real_signal.SIGINT, real_signal.SIG_DFL),
            (real_signal.SIGTERM, real_signal.SIG_DFL),
        ]

    def test_serve_installs_its_handlers_before_creating_the_server(self, server_module):
        """A signal that arrives while the server is being created still stops it normally."""
        real_signal = server_module.signal
        fake_server = MagicMock()
        installed_handlers = {}
        handlers_seen_by_create_server = []

        def create_server_during_which_a_signal_arrives(*arguments, **keyword_arguments):
            handlers_seen_by_create_server.append(dict(installed_handlers))
            installed_handlers[real_signal.SIGTERM](real_signal.SIGTERM, None)
            return fake_server

        with patch.object(
            server_module, "create_server",
            side_effect=create_server_during_which_a_signal_arrives,
        ), patch.object(
            real_signal, "signal",
            side_effect=lambda signal_number, handler: installed_handlers.__setitem__(
                signal_number, handler
            ),
        ), patch.object(server_module, "SHUTDOWN_POLL_INTERVAL", 0.01):
            server_module.serve(port=0)

        assert set(handlers_seen_by_create_server[0]) == {real_signal.SIGINT, real_signal.SIGTERM}
        fake_server.stop.assert_called_once_with(grace=5)

    def test_serve_restores_the_signal_handlers_if_creating_the_server_fails(
        self, server_module
    ):
        """create_server cleans up after itself; serve() has no server to stop."""
        real_signal = server_module.signal
        signal_calls = []

        def previous_handler(signal_number, frame):
            pass

        def recording_signal(signal_number, handler):
            signal_calls.append((signal_number, handler))
            return previous_handler

        with patch.object(
            server_module, "create_server", side_effect=RuntimeError("port in use")
        ), patch.object(real_signal, "signal", side_effect=recording_signal):
            with pytest.raises(RuntimeError, match="port in use"):
                server_module.serve(port=0)

        assert signal_calls[2:] == [
            (real_signal.SIGINT, previous_handler),
            (real_signal.SIGTERM, previous_handler),
        ]

    def test_serve_stops_the_server_and_raises_when_the_process_cannot_connect_any_more(
        self, server_module
    ):
        fake_server = MagicMock()
        callbacks_given = []

        def create_server_keeping_the_callback(*arguments, **keyword_arguments):
            callbacks_given.append(keyword_arguments["on_unrecoverable"])
            return fake_server

        # "Starting" the server is when the waiting thread gives up.
        fake_server.start.side_effect = lambda: callbacks_given[0]("connecting failed 3 times")

        with patch.object(
            server_module, "create_server", side_effect=create_server_keeping_the_callback
        ), patch.object(server_module, "SHUTDOWN_POLL_INTERVAL", 0.01), \
                patch.object(server_module.signal, "signal"):
            with pytest.raises(
                server_module.UnrecoverableConnectionError, match="connecting failed 3 times"
            ):
                server_module.serve(port=0)

        fake_server.stop.assert_called_once_with(grace=5)

    def test_main_exits_with_an_error_when_the_process_cannot_connect_any_more(
        self, server_module, caplog
    ):
        unrecoverable = server_module.UnrecoverableConnectionError(
            "This process can no longer connect to LinuxCNC and must be restarted: test"
        )
        with patch.object(server_module, "serve", side_effect=unrecoverable), \
                patch.object(sys, "argv", ["linuxcnc-grpc"]), \
                caplog.at_level(logging.ERROR, logger=server_module.logger.name):
            with pytest.raises(SystemExit) as exit_info:
                server_module.main()

        assert exit_info.value.code == 1
        assert "must be restarted" in caplog.text

    def test_command_line_flag(self, server_module):
        with patch.object(server_module, "serve") as serve, \
                patch.object(sys, "argv", ["linuxcnc-grpc"]):
            server_module.main()
        assert serve.call_args.kwargs["require_linuxcnc"] is False

        with patch.object(server_module, "serve") as serve, \
                patch.object(sys, "argv", ["linuxcnc-grpc", "--require-linuxcnc"]):
            server_module.main()
        assert serve.call_args.kwargs["require_linuxcnc"] is True


class TestReachabilityProbe:
    """The child-process check for a running LinuxCNC."""

    @pytest.fixture
    def unpatched_service_module(self, mock_linuxcnc_module):
        from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            linuxcnc_service = importlib.import_module("linuxcnc_grpc.linuxcnc_service")

            yield linuxcnc_service
        sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)

    @pytest.mark.parametrize("return_code, expected_reachable", [(0, True), (1, False)])
    def test_runs_a_child_python_and_goes_by_its_exit_code(
        self, unpatched_service_module, mock_linuxcnc_module, return_code, expected_reachable,
    ):
        completed_probe = MagicMock(returncode=return_code)
        servicer_class = unpatched_service_module.LinuxCNCServiceServicer
        with patch.object(
            unpatched_service_module.subprocess, "run", return_value=completed_probe
        ) as run_child:
            reachable = servicer_class._linuxcnc_is_reachable()

        assert reachable is expected_reachable
        child_command = run_child.call_args[0][0]
        assert child_command[0] == sys.executable
        assert child_command[1] == "-c"
        assert "linuxcnc.stat().poll()" in child_command[2]
        # The check must not touch the linuxcnc module in this process.
        mock_linuxcnc_module.stat.assert_not_called()

    def test_a_probe_that_cannot_run_or_hangs_counts_as_unreachable(
        self, unpatched_service_module
    ):
        import subprocess

        servicer_class = unpatched_service_module.LinuxCNCServiceServicer
        for failure in (OSError("no python"), subprocess.TimeoutExpired("python", 10)):
            with patch.object(unpatched_service_module.subprocess, "run", side_effect=failure):
                assert servicer_class._linuxcnc_is_reachable() is False

    def test_real_child_process_without_linuxcnc_reports_unreachable(
        self, unpatched_service_module
    ):
        """End to end: on a machine without the linuxcnc module the child fails cleanly."""
        import subprocess

        child_can_import_linuxcnc = subprocess.run(
            [sys.executable, "-c", "import linuxcnc"], capture_output=True, timeout=60
        ).returncode == 0
        if child_can_import_linuxcnc:
            pytest.skip("the linuxcnc module is installed here")

        servicer_class = unpatched_service_module.LinuxCNCServiceServicer
        assert servicer_class._linuxcnc_is_reachable() is False
