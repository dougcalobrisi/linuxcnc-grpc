"""
The command helper process, the two servicer locks, and the two
CommandResponse fields that describe what became of a command.

The helper processes started here are the real script
(command_helper_process.py). Their ``linuxcnc`` module is the stand-in in
tests/helper_stand_in, steered through the ``helper_stand_in`` fixture, so
no LinuxCNC is needed. Ordering between threads and processes is fixed with
events and FIFOs, never with sleeps.
"""

import importlib
import os
import pathlib
import signal
import sys
import threading
from concurrent import futures
from unittest.mock import MagicMock, patch

import grpc
import pytest

from linuxcnc_grpc import command_helper
from linuxcnc_grpc.command_helper import (
    COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE,
    CommandHelperLost,
    CommandNotHandedOver,
    HelperCommandChannel,
)
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

# Longest a test waits for another thread. Only reached when something is
# broken; a passing test never waits this long.
THREAD_WAIT_TIMEOUT = 20.0  # seconds
# The serial the stand-in LinuxCNC gives the first message it takes.
FIRST_STAND_IN_SERIAL = 101
HELPER_THAT_DOES_NOT_READ = (
    pathlib.Path(__file__).parent / "helper_stand_in" / "helper_that_does_not_read.py"
)


class StandInLinuxCNCError(Exception):
    """What the channel raises here for a linuxcnc.error in the helper."""


def process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def worker_threads():
    """A thread pool for the calls a test makes while another call is blocked."""
    pool = futures.ThreadPoolExecutor(max_workers=8)
    yield pool
    pool.shutdown(wait=False, cancel_futures=True)


@pytest.fixture
def make_channel():
    """Build helper channels and end their processes afterwards."""
    channels = []

    def _make_channel(start: bool = True) -> HelperCommandChannel:
        channel = HelperCommandChannel(linuxcnc_error_type=StandInLinuxCNCError)
        channels.append(channel)
        if start:
            channel.start()
        return channel

    yield _make_channel
    for channel in channels:
        channel.close()


# =============================================================================
# The channel and the helper process
# =============================================================================


class TestHelperChannel:
    def test_forwards_a_call_and_reports_the_serial(self, make_channel, helper_stand_in):
        channel = make_channel()
        assert channel.serial == 0
        assert process_exists(channel.process_id)

        channel.begin_command()
        channel.mode(3)
        channel.jog(1, False, 0, 2.5)

        assert helper_stand_in.calls() == [("mode", [3]), ("jog", [1, False, 0, 2.5])]
        assert channel.serial == FIRST_STAND_IN_SERIAL + 1
        assert channel.requests_handed_over == 2

    def test_helper_runs_in_a_session_of_its_own(self, make_channel):
        channel = make_channel()
        assert os.getsid(channel.process_id) == channel.process_id
        assert os.getsid(channel.process_id) != os.getsid(0)

    def test_linuxcnc_error_in_the_helper_is_raised_as_the_given_type(
        self, make_channel, helper_stand_in
    ):
        helper_stand_in.set_behaviour("mdi", "raise_linuxcnc_error")
        channel = make_channel()
        channel.begin_command()

        with pytest.raises(StandInLinuxCNCError, match="mdi failed in LinuxCNC"):
            channel.mdi("G0 X1")
        # The helper is still there and still usable.
        channel.mode(1)
        assert helper_stand_in.calls()[-1] == ("mode", [1])
        assert len(set(helper_stand_in.process_ids())) == 1

    def test_other_exception_keeps_its_type_and_text(self, make_channel, helper_stand_in):
        helper_stand_in.set_behaviour("jog", "raise_value_error")
        channel = make_channel()
        channel.begin_command()

        with pytest.raises(ValueError, match="jog: bad argument"):
            channel.jog(1, False, 0)

    def test_serial_stays_when_linuxcnc_does_not_take_the_write(
        self, make_channel, helper_stand_in
    ):
        channel = make_channel()
        channel.begin_command()
        channel.mode(3)
        helper_stand_in.set_behaviour("feedrate", "write_fails")

        channel.feedrate(0.5)

        assert channel.serial == FIRST_STAND_IN_SERIAL

    def test_output_on_descriptor_1_does_not_break_the_protocol(
        self, make_channel, helper_stand_in, capfd
    ):
        helper_stand_in.set_behaviour("mode", "print_to_stdout")
        channel = make_channel()
        channel.begin_command()

        channel.mode(3)
        channel.mode(1)

        assert channel.serial == FIRST_STAND_IN_SERIAL + 1
        # The helper's descriptor 1 is its standard error, which it shares
        # with this process.
        assert "noise from a C library" in capfd.readouterr().err

    def test_bindings_without_a_serial_report_none(self, make_channel, helper_stand_in):
        helper_stand_in.set_behaviour("command", "no_serial")
        channel = make_channel()
        channel.begin_command()

        channel.mode(3)

        assert channel.serial is None
        assert helper_stand_in.calls() == [("mode", [3])]

    def test_start_fails_when_the_helper_cannot_reach_linuxcnc(
        self, make_channel, helper_stand_in
    ):
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")
        channel = make_channel(start=False)

        with pytest.raises(CommandNotHandedOver, match="could not connect to LinuxCNC"):
            channel.start()
        assert channel.process_id is None

    def test_start_fails_when_the_helper_program_is_missing(self, make_channel, tmp_path):
        with patch.object(command_helper, "HELPER_SCRIPT_PATH", str(tmp_path / "missing.py")):
            channel = make_channel(start=False)
        with pytest.raises(CommandNotHandedOver, match="did not start"):
            channel.start()

    def test_helper_ignores_sigterm(self, make_channel, helper_stand_in):
        channel = make_channel()
        helper_process_id = channel.process_id

        os.kill(helper_process_id, signal.SIGTERM)
        channel.begin_command()
        channel.mode(3)
        channel.mode(1)

        assert helper_stand_in.process_ids() == [helper_process_id, helper_process_id]

    def test_unfinished_request_line_is_not_acted_on(self, make_channel, helper_stand_in):
        channel = make_channel()
        helper_popen = channel._process._popen

        os.write(helper_popen.stdin.fileno(), b'{"id": 1, "method": "mode", "args": [3]}')
        helper_popen.stdin.close()

        assert helper_popen.wait(timeout=THREAD_WAIT_TIMEOUT) == 0
        assert helper_stand_in.calls() == []

    def test_helper_that_died_while_idle_is_replaced_before_the_next_command(
        self, make_channel, helper_stand_in
    ):
        channel = make_channel()
        channel.begin_command()
        channel.mode(3)
        first_process_id = channel.process_id
        os.kill(first_process_id, signal.SIGKILL)
        channel._process._popen.wait(timeout=THREAD_WAIT_TIMEOUT)

        channel.begin_command()
        # A new helper's own command.serial starts at 0; the channel's
        # serial is still the last message LinuxCNC took.
        assert channel.serial == FIRST_STAND_IN_SERIAL
        channel.mode(1)

        assert channel.process_id != first_process_id
        assert channel.helper_was_lost is False
        assert channel.serial == FIRST_STAND_IN_SERIAL + 1
        assert helper_stand_in.calls() == [("mode", [3]), ("mode", [1])]

    @pytest.mark.parametrize("how_it_dies", ["exits by itself", "is killed"])
    def test_helper_lost_during_a_call_may_have_sent_the_command(
        self, make_channel, helper_stand_in, worker_threads, how_it_dies
    ):
        helper_stand_in.set_behaviour("mode", "block")
        channel = make_channel()
        lost_process_id = channel.process_id
        channel.begin_command()

        call = worker_threads.submit(channel.mode, 3)
        assert helper_stand_in.wait_until_entered() == "mode"
        if how_it_dies == "is killed":
            os.kill(lost_process_id, signal.SIGKILL)
        else:
            helper_stand_in.release("exit")

        with pytest.raises(CommandHelperLost) as lost:
            call.result(timeout=THREAD_WAIT_TIMEOUT)
        assert str(lost.value) == COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE
        assert "may have been sent" in str(lost.value)
        assert "not sent" not in str(lost.value)
        assert channel.helper_was_lost is True
        assert channel.process_id is None
        assert not process_exists(lost_process_id)

        helper_stand_in.set_behaviour("mode", "")
        channel.restart_if_lost()
        assert channel.process_id not in (None, lost_process_id)
        channel.begin_command()
        channel.mode(1)
        assert helper_stand_in.calls()[-1] == ("mode", [1])

    def test_request_a_helper_does_not_read_is_given_up_and_not_sent(
        self, monkeypatch, tmp_path
    ):
        """
        A request larger than the pipe buffer, to a helper that has stopped
        reading, would block the write for ever. It gives up at the
        deadline; the newline never got through, so nothing was sent.
        """
        monkeypatch.setattr(command_helper, "HELPER_WRITE_TIMEOUT", 0.3)
        channel = HelperCommandChannel(
            linuxcnc_error_type=StandInLinuxCNCError,
            helper_argv=[sys.executable, str(HELPER_THAT_DOES_NOT_READ)],
        )
        try:
            channel.start()
            stuck_process_id = channel.process_id
            channel.begin_command()

            with pytest.raises(CommandNotHandedOver, match="could not be handed"):
                channel.text_msg("x" * (4 * 1024 * 1024))

            assert channel.helper_was_lost is False
            assert channel.process_id is None
            assert not process_exists(stuck_process_id)
        finally:
            channel.close()

    def test_write_deadline_holds_while_the_helper_takes_a_little_at_a_time(
        self, make_channel, helper_stand_in
    ):
        """
        Steady partial progress must not run past the deadline. Simulated
        time: each look at the clock is one second later, each write takes
        one byte.
        """
        channel = make_channel()
        process = channel._process
        simulated_seconds = iter(range(1000))
        real_write = os.write

        def write_one_byte(descriptor, data):
            return real_write(descriptor, data[:1])

        request_line = b'{"id": 1, "method": "mode", "args": [3]}\n'
        with patch.object(command_helper.time, "monotonic", lambda: next(simulated_seconds)), \
                patch.object(command_helper.os, "write", write_one_byte):
            with pytest.raises(TimeoutError, match=f"of {len(request_line)} bytes"):
                process.write_request(request_line, 5.0)
        channel.close()

        assert helper_stand_in.calls() == []

    def test_a_line_written_whole_counts_as_handed_over(self, make_channel, helper_stand_in):
        """Once the newline is written, no deadline turns it into "not sent"."""
        channel = make_channel()
        channel.begin_command()
        clock_readings = iter([0.0] + [100.0] * 1000)

        with patch.object(command_helper.time, "monotonic", lambda: next(clock_readings)):
            channel._process.write_request(b'{"id": 1, "method": "mode", "args": [3]}\n', 5.0)

        assert channel._process.read_reply(THREAD_WAIT_TIMEOUT)["ok"] is True
        assert helper_stand_in.calls() == [("mode", [3])]

    def test_close_ends_an_idle_helper(self, make_channel):
        channel = make_channel()
        helper_process_id = channel.process_id

        channel.close()

        assert not process_exists(helper_process_id)
        assert channel.process_id is None
        channel.close()  # harmless a second time

    def test_close_during_a_call_leaves_the_helper_to_finish_it(
        self, make_channel, helper_stand_in, worker_threads
    ):
        helper_stand_in.set_behaviour("mode", "block")
        channel = make_channel()
        helper_process_id = channel.process_id
        channel.begin_command()
        call = worker_threads.submit(channel.mode, 3)
        helper_stand_in.wait_until_entered()

        channel.close()

        assert not call.done()
        assert process_exists(helper_process_id)
        helper_stand_in.release()
        call.result(timeout=THREAD_WAIT_TIMEOUT)
        assert channel.serial == FIRST_STAND_IN_SERIAL
        # The thread that waited for the reply ended the helper.
        assert not process_exists(helper_process_id)

    def test_call_after_close_gets_a_helper_of_its_own(self, make_channel, helper_stand_in):
        channel = make_channel()
        first_process_id = channel.process_id
        channel.close()

        channel.begin_command()
        assert channel.process_id is None  # nothing is started until a call needs it
        channel.abort()
        channel.abort()

        assert helper_stand_in.calls() == [("abort", []), ("abort", [])]
        process_ids_used = helper_stand_in.process_ids()
        assert first_process_id not in process_ids_used
        assert process_ids_used[0] != process_ids_used[1]
        assert not any(process_exists(process_id) for process_id in process_ids_used)
        assert channel.serial == FIRST_STAND_IN_SERIAL + 1


# =============================================================================
# The servicer
# =============================================================================


class InProcessCommand:
    """A linuxcnc.command() stand-in for a servicer that sends in-process."""

    def __init__(self, has_serial: bool = True):
        if has_serial:
            self.serial = 500
        self.calls = []
        self.methods_that_raise = {}
        self.methods_whose_write_fails = set()

    def __getattr__(self, method_name):
        if method_name.startswith("_") or method_name == "serial":
            raise AttributeError(method_name)

        def method(*arguments):
            self.calls.append((method_name, arguments))
            if method_name in self.methods_that_raise:
                raise self.methods_that_raise[method_name]
            if method_name in self.methods_whose_write_fails:
                return
            if "serial" in self.__dict__:
                self.serial += 1

        return method


class LockThatReportsWaiting:
    """Wraps a lock and sets an event when an attempt to take it fails."""

    def __init__(self, wrapped_lock):
        self._wrapped_lock = wrapped_lock
        self.an_attempt_failed = threading.Event()

    def acquire(self, *arguments, **keyword_arguments):
        acquired = self._wrapped_lock.acquire(*arguments, **keyword_arguments)
        if not acquired:
            self.an_attempt_failed.set()
        return acquired

    def release(self):
        self._wrapped_lock.release()

    def __enter__(self):
        self._wrapped_lock.acquire()
        return self

    def __exit__(self, *exception_details):
        self._wrapped_lock.release()


@pytest.fixture
def service_module(mock_linuxcnc_module, mock_linuxcnc_stat, mock_linuxcnc_error_channel):
    """The service module bound to the mocked linuxcnc module."""
    # Requests to the helper are JSON, so every constant a handler passes on
    # has to be a real number here, as it is in LinuxCNC's module.
    for constant_number, constant_name in enumerate(
        (
            "JOG_STOP", "JOG_CONTINUOUS", "JOG_INCREMENT",
            "SPINDLE_FORWARD", "SPINDLE_REVERSE", "SPINDLE_OFF", "SPINDLE_INCREASE",
            "SPINDLE_DECREASE", "SPINDLE_CONSTANT", "BRAKE_ENGAGE", "BRAKE_RELEASE",
            "MIST_ON", "MIST_OFF", "FLOOD_ON", "FLOOD_OFF",
            "AUTO_RUN", "AUTO_PAUSE", "AUTO_RESUME", "AUTO_STEP",
        ),
        start=100,
    ):
        setattr(mock_linuxcnc_module, constant_name, constant_number)
    mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
    mock_linuxcnc_module.command.side_effect = InProcessCommand
    mock_linuxcnc_module.error_channel.return_value = mock_linuxcnc_error_channel
    with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
        sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
        yield importlib.import_module("linuxcnc_grpc.linuxcnc_service")
    sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)


@pytest.fixture
def make_service(service_module):
    """Build servicers and close them (helper included) afterwards."""
    services = []

    def _make_service(**servicer_arguments):
        servicer_arguments.setdefault("error_channel_mode", "off")
        service = service_module.LinuxCNCServiceServicer(**servicer_arguments)
        services.append(service)
        return service

    yield _make_service
    for service in services:
        service.close()


@pytest.fixture
def helper_service(make_service):
    """A connected servicer that sends through a helper process."""
    return make_service(command_helper=True)


def active_context():
    context = MagicMock(spec=grpc.ServicerContext)
    context.is_active.return_value = True
    return context


def mode_command(mode=linuxcnc_pb2.MODE_MDI):
    return linuxcnc_pb2.LinuxCNCCommand(mode=linuxcnc_pb2.ModeCommand(mode=mode))


def feedrate_command(scale=0.5):
    return linuxcnc_pb2.LinuxCNCCommand(feedrate=linuxcnc_pb2.FeedrateCommand(scale=scale))


def coolant_command():
    return linuxcnc_pb2.LinuxCNCCommand(
        coolant=linuxcnc_pb2.CoolantCommand(mist=True, flood=True)
    )


def estop_command():
    return linuxcnc_pb2.LinuxCNCCommand(
        state=linuxcnc_pb2.StateCommand(state=linuxcnc_pb2.STATE_ESTOP)
    )


def command_with_a_bad_number():
    """Refused by the handler's validation, before the command channel."""
    return feedrate_command(scale=float("nan"))


class TestSendingThroughTheHelper:
    def test_command_goes_through_the_helper(self, helper_service, helper_stand_in):
        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.linuxcnc_serial == FIRST_STAND_IN_SERIAL
        assert not response.not_sent
        assert helper_stand_in.calls() == [("mode", [3])]
        assert helper_service._latest_dispatch_record.linuxcnc_serial == FIRST_STAND_IN_SERIAL
        assert list(helper_service._issued_linuxcnc_serials) == [FIRST_STAND_IN_SERIAL]

    def test_directly_constructed_servicer_sends_in_process(
        self, make_service, helper_stand_in
    ):
        service = make_service()

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert isinstance(service._command_channel, InProcessCommand)
        assert service._command_channel.calls == [("mode", (3,))]
        assert helper_stand_in.calls() == []

    def test_linuxcnc_error_from_the_helper_keeps_its_text(
        self, helper_service, helper_stand_in
    ):
        helper_stand_in.set_behaviour("mdi", "raise_linuxcnc_error")

        response = helper_service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(mdi=linuxcnc_pb2.MdiCommand(command="G0 X1")),
            active_context(),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "mdi failed in LinuxCNC"

    def test_bindings_without_a_serial_use_the_fallback_record(
        self, make_service, helper_stand_in, mock_linuxcnc_stat
    ):
        """LinuxCNC 2.8 style bindings: no linuxcnc_serial, no acknowledged."""
        helper_stand_in.set_behaviour("command", "no_serial")
        mock_linuxcnc_stat.echo_serial_number = 77
        service = make_service(command_helper=True)

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not response.HasField("linuxcnc_serial")
        assert not response.HasField("acknowledged")
        assert not response.not_sent
        assert helper_stand_in.calls() == [("mode", [3])]
        record = service._latest_dispatch_record
        assert record.linuxcnc_serial is None
        assert record.echo_serial_before_dispatch == 77

    def test_helper_is_started_before_anything_is_tried_in_process(
        self, make_service, helper_stand_in, mock_linuxcnc_module
    ):
        """A helper that cannot reach LinuxCNC fails the connect by itself."""
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")

        with pytest.raises(CommandNotHandedOver, match="could not connect to LinuxCNC"):
            make_service(command_helper=True)

        mock_linuxcnc_module.stat.assert_not_called()

    def test_failed_in_process_connect_ends_the_new_helper(
        self, service_module, mock_linuxcnc_stat
    ):
        mock_linuxcnc_stat.poll.side_effect = RuntimeError("status channel gone")
        started_channels = []
        real_start = HelperCommandChannel.start

        def start_and_remember(channel):
            started_channels.append(channel)
            real_start(channel)
            started_channels.append(channel.process_id)

        with patch.object(HelperCommandChannel, "start", start_and_remember):
            with pytest.raises(RuntimeError, match="status channel gone"):
                service_module.LinuxCNCServiceServicer(
                    command_helper=True, error_channel_mode="off"
                )

        channel, helper_process_id = started_channels
        assert channel.process_id is None
        assert not process_exists(helper_process_id)

    def test_reconnect_replaces_the_helper_and_ends_the_old_one(self, helper_service):
        first_channel = helper_service._command_channel
        first_process_id = first_channel.process_id

        assert helper_service._connect() is True

        assert helper_service._command_channel is not first_channel
        assert process_exists(helper_service._command_channel.process_id)
        assert not process_exists(first_process_id)

    def test_connect_after_close_publishes_nothing_and_ends_its_helper(self, helper_service):
        helper_service.close()
        closed_channel = helper_service._command_channel
        generation_before = helper_service._connection_generation
        started_process_ids = []
        real_start = HelperCommandChannel.start

        def start_and_remember(channel):
            real_start(channel)
            started_process_ids.append(channel.process_id)

        with patch.object(HelperCommandChannel, "start", start_and_remember):
            assert helper_service._connect() is False

        assert helper_service._command_channel is closed_channel
        assert helper_service._connection_generation == generation_before
        assert len(started_process_ids) == 1
        assert not process_exists(started_process_ids[0])


class TestLocks:
    """A blocked send holds the command lock and nothing else."""

    @pytest.fixture
    def blocked_send(self, helper_service, helper_stand_in, worker_threads):
        """A SendCommand whose helper call is waiting for helper_stand_in.release()."""
        helper_stand_in.set_behaviour("mode", "block")
        send = worker_threads.submit(
            helper_service.SendCommand, mode_command(), active_context()
        )
        assert helper_stand_in.wait_until_entered() == "mode"
        yield send
        if not send.done():
            helper_stand_in.release()
            send.result(timeout=THREAD_WAIT_TIMEOUT)

    def test_status_is_answered_while_a_send_is_blocked(
        self, helper_service, blocked_send, worker_threads
    ):
        status = worker_threads.submit(
            helper_service.GetStatus, linuxcnc_pb2.GetStatusRequest(), active_context()
        ).result(timeout=THREAD_WAIT_TIMEOUT)

        assert isinstance(status, linuxcnc_pb2.LinuxCNCStatus)
        assert not blocked_send.done()

    def test_state_lock_is_free_while_a_send_is_blocked(self, helper_service, blocked_send):
        """What the error reader, ResolveFile and GetMachineConfig need."""
        assert helper_service._lock.acquire(timeout=THREAD_WAIT_TIMEOUT)
        helper_service._lock.release()
        assert helper_service._poll_error_channel() is None
        assert not blocked_send.done()

    def test_stream_status_sends_updates_while_a_send_is_blocked(
        self, helper_service, blocked_send, worker_threads
    ):
        def first_update():
            stream = helper_service.StreamStatus(
                linuxcnc_pb2.StreamStatusRequest(interval_ms=10), active_context()
            )
            try:
                return next(stream)
            finally:
                stream.close()

        update = worker_threads.submit(first_update).result(timeout=THREAD_WAIT_TIMEOUT)

        assert isinstance(update, linuxcnc_pb2.LinuxCNCStatus)
        assert not blocked_send.done()

    def test_targeted_wait_on_an_earlier_command_answers_while_a_send_is_blocked(
        self, helper_service, mock_linuxcnc_stat, worker_threads, helper_stand_in
    ):
        """A wait that names a serial is not held up by the command being sent."""
        earlier = helper_service.SendCommand(feedrate_command(), active_context())
        mock_linuxcnc_stat.echo_serial_number = earlier.linuxcnc_serial
        helper_stand_in.set_behaviour("mode", "block")
        send = worker_threads.submit(helper_service.SendCommand, mode_command(), active_context())
        helper_stand_in.wait_until_entered()

        response = worker_threads.submit(
            helper_service.WaitComplete,
            linuxcnc_pb2.WaitCompleteRequest(
                timeout=30, linuxcnc_serial=earlier.linuxcnc_serial
            ),
            active_context(),
        ).result(timeout=THREAD_WAIT_TIMEOUT)

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not send.done()
        helper_stand_in.release()
        send.result(timeout=THREAD_WAIT_TIMEOUT)

    def test_second_command_waits_for_the_first(
        self, helper_service, helper_stand_in, blocked_send, worker_threads
    ):
        observed_command_lock = LockThatReportsWaiting(helper_service._command_lock)
        helper_service._command_lock = observed_command_lock

        second_send = worker_threads.submit(
            helper_service.SendCommand, feedrate_command(), active_context()
        )
        assert observed_command_lock.an_attempt_failed.wait(THREAD_WAIT_TIMEOUT)
        assert helper_stand_in.calls() == [("mode", [3])]
        assert not second_send.done()

        helper_stand_in.release()
        first_response = blocked_send.result(timeout=THREAD_WAIT_TIMEOUT)
        second_response = second_send.result(timeout=THREAD_WAIT_TIMEOUT)

        assert helper_stand_in.calls() == [("mode", [3]), ("feedrate", [0.5])]
        assert first_response.linuxcnc_serial == FIRST_STAND_IN_SERIAL
        assert second_response.linuxcnc_serial == FIRST_STAND_IN_SERIAL + 1

    def test_messages_of_one_command_are_not_interleaved_with_another(
        self, helper_service, helper_stand_in, worker_threads
    ):
        helper_stand_in.set_behaviour("mist", "block")
        observed_command_lock = LockThatReportsWaiting(helper_service._command_lock)
        helper_service._command_lock = observed_command_lock
        coolant_send = worker_threads.submit(
            helper_service.SendCommand, coolant_command(), active_context()
        )
        helper_stand_in.wait_until_entered()
        feedrate_send = worker_threads.submit(
            helper_service.SendCommand, feedrate_command(), active_context()
        )
        assert observed_command_lock.an_attempt_failed.wait(THREAD_WAIT_TIMEOUT)

        helper_stand_in.release()
        coolant_response = coolant_send.result(timeout=THREAD_WAIT_TIMEOUT)
        feedrate_send.result(timeout=THREAD_WAIT_TIMEOUT)

        assert [method_name for method_name, _ in helper_stand_in.calls()] == [
            "mist", "flood", "feedrate",
        ]
        # The serial of the last of the command's two messages.
        assert coolant_response.linuxcnc_serial == FIRST_STAND_IN_SERIAL + 1

    def test_waiting_command_whose_client_went_away_gives_up_its_place(
        self, helper_service, helper_stand_in, blocked_send, worker_threads, service_module
    ):
        context_of_a_gone_client = MagicMock(spec=grpc.ServicerContext)
        context_of_a_gone_client.is_active.return_value = False

        response = worker_threads.submit(
            helper_service.SendCommand, feedrate_command(), context_of_a_gone_client
        ).result(timeout=THREAD_WAIT_TIMEOUT)

        assert not blocked_send.done()
        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == service_module.REQUEST_NO_LONGER_ACTIVE_MESSAGE
        assert response.not_sent
        assert helper_stand_in.calls() == [("mode", [3])]

    def test_waiting_stop_is_sent_although_its_client_went_away(
        self, helper_service, helper_stand_in, blocked_send, worker_threads
    ):
        observed_command_lock = LockThatReportsWaiting(helper_service._command_lock)
        helper_service._command_lock = observed_command_lock
        context_of_a_gone_client = MagicMock(spec=grpc.ServicerContext)
        context_of_a_gone_client.is_active.return_value = False

        stop_send = worker_threads.submit(
            helper_service.SendCommand, estop_command(), context_of_a_gone_client
        )
        assert observed_command_lock.an_attempt_failed.wait(THREAD_WAIT_TIMEOUT)
        assert not stop_send.done()
        helper_stand_in.release()

        assert stop_send.result(timeout=THREAD_WAIT_TIMEOUT).status == linuxcnc_pb2.RCS_DONE
        assert helper_stand_in.calls()[-1][0] == "state"

    def test_connect_waits_for_a_command_and_gives_up_when_the_servicer_closes(
        self, helper_service, blocked_send, worker_threads
    ):
        """The command channel is never replaced under a command."""
        channel_in_use = helper_service._command_channel
        observed_command_lock = LockThatReportsWaiting(helper_service._command_lock)
        helper_service._command_lock = observed_command_lock

        connect = worker_threads.submit(
            helper_service._connect_if_unchanged_since, helper_service._connection_snapshot()
        )
        assert observed_command_lock.an_attempt_failed.wait(THREAD_WAIT_TIMEOUT)
        assert not connect.done()
        helper_service._stop_requested.set()

        assert connect.result(timeout=THREAD_WAIT_TIMEOUT) is False
        assert helper_service._command_channel is channel_in_use
        assert not blocked_send.done()

    def test_validation_reads_the_status_under_the_state_lock(
        self, make_service, mock_linuxcnc_stat
    ):
        """A handler's index check must not see a status another thread is polling."""
        service = make_service()
        lock_was_held_during_poll = []
        mock_linuxcnc_stat.poll.side_effect = lambda: lock_was_held_during_poll.append(
            service._lock._is_owned()
        )

        service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(home=linuxcnc_pb2.HomeCommand(joint=0)),
            active_context(),
        )

        assert lock_was_held_during_poll
        assert all(lock_was_held_during_poll)


class TestWaitCompleteDuringASend:
    """
    An untargeted WaitComplete made while a command is being sent waits on
    that command (rule 0), as in 1.0, without holding any lock.
    """

    @pytest.fixture
    def wait_sleeping(self, service_module):
        """An event set once a WaitComplete has decided to keep waiting."""
        first_sleep_started = threading.Event()
        real_sleep = service_module.time.sleep

        def sleep_and_report(seconds):
            first_sleep_started.set()
            real_sleep(seconds)

        with patch.object(service_module.time, "sleep", sleep_and_report):
            yield first_sleep_started

    @pytest.fixture
    def blocked_mode_change(self, helper_service, helper_stand_in, worker_threads):
        helper_stand_in.set_behaviour("mode", "block")
        send = worker_threads.submit(helper_service.SendCommand, mode_command(), active_context())
        assert helper_stand_in.wait_until_entered() == "mode"
        yield send
        if not send.done():
            helper_stand_in.release()
            send.result(timeout=THREAD_WAIT_TIMEOUT)

    def test_answers_rcs_exec_at_its_timeout_while_the_send_lasts(
        self, helper_service, blocked_mode_change, worker_threads
    ):
        response = worker_threads.submit(
            helper_service.WaitComplete,
            linuxcnc_pb2.WaitCompleteRequest(serial=5, timeout=0.3),
            active_context(),
        ).result(timeout=THREAD_WAIT_TIMEOUT)

        # Before, it answered RCS_DONE at once, about the commands before.
        assert response.status == linuxcnc_pb2.RCS_EXEC
        assert response.error_message == ""
        assert not blocked_mode_change.done()

    def test_follows_the_command_once_its_send_returns(
        self, helper_service, helper_stand_in, mock_linuxcnc_stat, blocked_mode_change,
        worker_threads, wait_sleeping, service_module,
    ):
        # The task will have echoed the mode change and finished it.
        mock_linuxcnc_stat.echo_serial_number = FIRST_STAND_IN_SERIAL
        mock_linuxcnc_stat.state = service_module.linuxcnc.RCS_DONE
        wait = worker_threads.submit(
            helper_service.WaitComplete,
            linuxcnc_pb2.WaitCompleteRequest(timeout=30),
            active_context(),
        )
        assert wait_sleeping.wait(THREAD_WAIT_TIMEOUT)
        assert not wait.done()

        helper_stand_in.release()

        assert blocked_mode_change.result(timeout=THREAD_WAIT_TIMEOUT).linuxcnc_serial == (
            FIRST_STAND_IN_SERIAL
        )
        assert wait.result(timeout=THREAD_WAIT_TIMEOUT).status == linuxcnc_pb2.RCS_DONE

    def test_applies_the_rules_to_the_new_record_once_the_send_returns(
        self, helper_service, helper_stand_in, mock_linuxcnc_stat, blocked_mode_change,
        worker_threads, wait_sleeping,
    ):
        """The task has not taken the command: the wait goes on to its timeout."""
        mock_linuxcnc_stat.echo_serial_number = FIRST_STAND_IN_SERIAL - 1
        wait = worker_threads.submit(
            helper_service.WaitComplete,
            linuxcnc_pb2.WaitCompleteRequest(timeout=1.0),
            active_context(),
        )
        assert wait_sleeping.wait(THREAD_WAIT_TIMEOUT)
        helper_stand_in.release()

        assert blocked_mode_change.result(timeout=THREAD_WAIT_TIMEOUT).acknowledged is False
        assert wait.result(timeout=THREAD_WAIT_TIMEOUT).status == linuxcnc_pb2.RCS_EXEC

    def test_keeps_to_the_command_it_captured_when_a_later_one_also_finished(
        self, helper_service, helper_stand_in, mock_linuxcnc_stat, blocked_mode_change,
        worker_threads, service_module,
    ):
        """
        A (the blocked mode change) and then B finish before the wait looks
        again. The rules apply to A's record: the echo is past A, so DONE,
        although B, the latest, is in error.
        """
        wait_paused = threading.Event()
        let_the_wait_go_on = threading.Event()
        real_sleep = service_module.time.sleep

        def sleep_once_paused(seconds):
            if not wait_paused.is_set():
                wait_paused.set()
                let_the_wait_go_on.wait(THREAD_WAIT_TIMEOUT)
            real_sleep(seconds)

        with patch.object(service_module.time, "sleep", sleep_once_paused):
            wait = worker_threads.submit(
                helper_service.WaitComplete,
                linuxcnc_pb2.WaitCompleteRequest(timeout=30),
                active_context(),
            )
            assert wait_paused.wait(THREAD_WAIT_TIMEOUT)
            helper_stand_in.release()
            command_a = blocked_mode_change.result(timeout=THREAD_WAIT_TIMEOUT)
            command_b = helper_service.SendCommand(feedrate_command(), active_context())
            mock_linuxcnc_stat.echo_serial_number = command_b.linuxcnc_serial
            mock_linuxcnc_stat.state = service_module.linuxcnc.RCS_ERROR
            let_the_wait_go_on.set()

            response = wait.result(timeout=THREAD_WAIT_TIMEOUT)

        assert (command_a.linuxcnc_serial, command_b.linuxcnc_serial) == (
            FIRST_STAND_IN_SERIAL, FIRST_STAND_IN_SERIAL + 1
        )
        assert response.status == linuxcnc_pb2.RCS_DONE

    def test_finished_records_are_kept_for_a_bounded_number_of_commands(
        self, helper_service, service_module, mock_linuxcnc_stat, monkeypatch
    ):
        monkeypatch.setattr(service_module, "FINISHED_DISPATCHES_REMEMBERED", 2)
        for _ in range(3):
            helper_service._begin_outstanding_dispatch()
            helper_service._finish_outstanding_dispatch()

        assert list(helper_service._finished_dispatch_records) == [2, 3]

    def test_command_lost_with_its_helper_leaves_the_previous_record(
        self, helper_service, helper_stand_in, mock_linuxcnc_stat, worker_threads,
        wait_sleeping,
    ):
        """
        What became of a command lost with its helper is not known. The mark
        is cleared and the wait reports on the command before, as the
        response to the lost command tells its client to check the status.
        """
        earlier = helper_service.SendCommand(feedrate_command(), active_context())
        mock_linuxcnc_stat.echo_serial_number = earlier.linuxcnc_serial + 5
        helper_stand_in.set_behaviour("mode", "block")
        send = worker_threads.submit(helper_service.SendCommand, mode_command(), active_context())
        helper_stand_in.wait_until_entered()
        wait = worker_threads.submit(
            helper_service.WaitComplete,
            linuxcnc_pb2.WaitCompleteRequest(timeout=30),
            active_context(),
        )
        assert wait_sleeping.wait(THREAD_WAIT_TIMEOUT)

        helper_stand_in.release("exit")

        assert send.result(timeout=THREAD_WAIT_TIMEOUT).error_message == (
            COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE
        )
        assert wait.result(timeout=THREAD_WAIT_TIMEOUT).status == linuxcnc_pb2.RCS_DONE
        assert helper_service._outstanding_dispatch is None
        assert helper_service._latest_dispatch_record.linuxcnc_serial == earlier.linuxcnc_serial

    @pytest.mark.parametrize(
        "how_the_command_ends",
        ["dispatched", "refused by validation", "not taken", "raised", "partly sent"],
    )
    def test_mark_is_cleared_on_every_path(
        self, make_service, how_the_command_ends
    ):
        service = make_service()
        request = mode_command()
        if how_the_command_ends == "refused by validation":
            request = command_with_a_bad_number()
        elif how_the_command_ends == "not taken":
            service._command_channel.methods_whose_write_fails.add("mode")
        elif how_the_command_ends == "raised":
            service._command_channel.methods_that_raise["mode"] = RuntimeError("failed")
        elif how_the_command_ends == "partly sent":
            request = coolant_command()
            service._command_channel.methods_that_raise["flood"] = RuntimeError("failed")
        marks_seen_by_the_handler = []
        real_handlers = dict(service._command_handlers)

        def handler_that_looks(command_request):
            marks_seen_by_the_handler.append(service._outstanding_dispatch)
            real_handlers[command_request.WhichOneof("command")](command_request)

        service._command_handlers = {name: handler_that_looks for name in real_handlers}

        service.SendCommand(request, active_context())

        assert marks_seen_by_the_handler and marks_seen_by_the_handler[0] is not None
        assert service._outstanding_dispatch is None

    def test_mark_is_cleared_when_the_bookkeeping_fails(self, make_service):
        service = make_service()

        with patch.object(service, "_record_dispatch", side_effect=RuntimeError("failed")):
            service.SendCommand(mode_command(), active_context())

        assert service._outstanding_dispatch is None


class TestAcknowledged:
    def test_true_when_the_task_has_echoed_the_serial(
        self, helper_service, mock_linuxcnc_stat
    ):
        mock_linuxcnc_stat.echo_serial_number = FIRST_STAND_IN_SERIAL

        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.HasField("acknowledged")
        assert response.acknowledged is True

    def test_true_when_the_echo_is_already_past_the_serial(
        self, helper_service, mock_linuxcnc_stat
    ):
        mock_linuxcnc_stat.echo_serial_number = FIRST_STAND_IN_SERIAL + 3

        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.acknowledged is True

    def test_false_when_the_task_has_not_echoed_it(
        self, helper_service, mock_linuxcnc_stat, caplog
    ):
        mock_linuxcnc_stat.echo_serial_number = FIRST_STAND_IN_SERIAL - 1

        with caplog.at_level("WARNING"):
            response = helper_service.SendCommand(mode_command(), active_context())

        # Still dispatched: the status does not change.
        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.linuxcnc_serial == FIRST_STAND_IN_SERIAL
        assert response.HasField("acknowledged")
        assert response.acknowledged is False
        assert not response.not_sent
        assert "has not taken the command" in caplog.text

    def test_comparison_is_a_signed_32_bit_difference(self, make_service, mock_linuxcnc_stat):
        """An echo serial that wrapped past the largest int is ahead, not behind."""
        service = make_service()
        service._command_channel.serial = 2**31 - 2  # the command gets 2**31 - 1
        mock_linuxcnc_stat.echo_serial_number = -(2**31)

        response = service.SendCommand(mode_command(), active_context())

        assert response.linuxcnc_serial == 2**31 - 1
        assert response.acknowledged is True

    def test_describes_the_last_message_of_a_command_of_several(
        self, helper_service, mock_linuxcnc_stat
    ):
        """Coolant is two messages; the task has echoed only the first."""
        mock_linuxcnc_stat.echo_serial_number = FIRST_STAND_IN_SERIAL

        response = helper_service.SendCommand(coolant_command(), active_context())

        assert response.linuxcnc_serial == FIRST_STAND_IN_SERIAL + 1
        assert response.acknowledged is False

    def test_unset_when_the_bindings_have_no_serial(self, service_module, make_service):
        with patch.object(
            service_module.linuxcnc, "command",
            side_effect=lambda: InProcessCommand(has_serial=False),
        ):
            service = make_service()

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not response.HasField("acknowledged")

    def test_unset_when_nothing_was_dispatched(self, helper_service):
        response = helper_service.SendCommand(command_with_a_bad_number(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert not response.HasField("acknowledged")

    def test_unset_when_the_echo_serial_is_not_a_number(
        self, helper_service, mock_linuxcnc_stat
    ):
        mock_linuxcnc_stat.echo_serial_number = None

        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not response.HasField("acknowledged")

    def test_failed_status_poll_after_the_send_does_not_fail_the_command(
        self, make_service, mock_linuxcnc_stat
    ):
        service = make_service()
        mock_linuxcnc_stat.poll.side_effect = RuntimeError("status channel gone")

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.linuxcnc_serial == 501
        assert not response.HasField("acknowledged")
        assert service._latest_dispatch_record.linuxcnc_serial == 501


class TestNotSent:
    """not_sent is true exactly when it is certain nothing reached LinuxCNC."""

    def test_true_for_a_request_refused_by_validation(self, make_service):
        service = make_service()

        response = service.SendCommand(command_with_a_bad_number(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "finite" in response.error_message
        assert response.not_sent
        assert service._command_channel.calls == []

    def test_true_for_an_index_refused_before_a_command_of_several_messages(
        self, make_service
    ):
        service = make_service()

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                set_limit=linuxcnc_pb2.SetLimitCommand(joint=99, min_limit=0, max_limit=1)
            ),
            active_context(),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.not_sent
        assert service._command_channel.calls == []

    def test_true_for_a_request_with_no_command(self, make_service):
        service = make_service()

        response = service.SendCommand(linuxcnc_pb2.LinuxCNCCommand(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "Unknown command type: None"
        assert response.not_sent

    def test_true_for_a_command_refused_because_the_server_is_stopping(
        self, make_service, service_module
    ):
        service = make_service()
        service.close()

        response = service.SendCommand(mode_command(), active_context())

        assert response.error_message == service_module.SERVER_SHUTTING_DOWN_MESSAGE
        assert response.not_sent
        assert service._command_channel.calls == []

    def test_true_for_a_command_whose_client_has_gone(self, make_service, service_module):
        service = make_service()
        context_of_a_gone_client = MagicMock(spec=grpc.ServicerContext)
        context_of_a_gone_client.is_active.return_value = False

        response = service.SendCommand(mode_command(), context_of_a_gone_client)

        assert response.error_message == service_module.REQUEST_NO_LONGER_ACTIVE_MESSAGE
        assert response.not_sent

    def test_true_when_linuxcnc_did_not_take_a_single_message_command(
        self, make_service, service_module
    ):
        service = make_service()
        service._command_channel.methods_whose_write_fails.add("mode")

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == service_module.COMMAND_NOT_ACCEPTED_MESSAGE
        assert response.not_sent
        assert not response.HasField("linuxcnc_serial")
        assert service._latest_dispatch_record is None

    def test_true_when_linuxcnc_took_none_of_a_command_of_several_messages(
        self, make_service, service_module
    ):
        service = make_service()
        service._command_channel.methods_whose_write_fails.update({"mist", "flood"})

        response = service.SendCommand(coolant_command(), active_context())

        assert response.error_message == service_module.COMMAND_NOT_ACCEPTED_MESSAGE
        assert response.not_sent

    def test_true_when_the_same_happens_through_the_helper(
        self, helper_service, helper_stand_in, service_module
    ):
        helper_stand_in.set_behaviour("mode", "write_fails")

        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.error_message == service_module.COMMAND_NOT_ACCEPTED_MESSAGE
        assert response.not_sent

    def test_true_when_the_status_poll_before_a_fallback_dispatch_fails(
        self, service_module, make_service, mock_linuxcnc_stat
    ):
        with patch.object(
            service_module.linuxcnc, "command",
            side_effect=lambda: InProcessCommand(has_serial=False),
        ):
            service = make_service()
        mock_linuxcnc_stat.poll.side_effect = RuntimeError("status channel gone")

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "status channel gone"
        assert response.not_sent
        assert service._command_channel.calls == []

    def test_true_when_a_command_call_fails_and_the_serial_did_not_move(self, make_service):
        """The binding refused its arguments: it raised before writing."""
        service = make_service()
        service._command_channel.methods_that_raise["mode"] = TypeError("bad arguments")

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "bad arguments"
        assert response.not_sent

    def test_true_when_no_helper_can_be_started_for_the_command(
        self, helper_service, helper_stand_in
    ):
        helper_process_id = helper_service._command_channel.process_id
        os.kill(helper_process_id, signal.SIGKILL)
        helper_service._command_channel._process._popen.wait(timeout=THREAD_WAIT_TIMEOUT)
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")

        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "could not connect to LinuxCNC" in response.error_message
        assert response.not_sent
        assert helper_stand_in.calls() == []

    def test_true_when_the_request_could_not_be_written_to_the_helper(
        self, helper_service, helper_stand_in
    ):
        """The helper acts only on a whole line, and this one never got there."""
        with patch.object(
            command_helper._HelperProcess, "write_request",
            side_effect=BrokenPipeError("Broken pipe"),
        ):
            response = helper_service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "could not be handed to the command helper process" in response.error_message
        assert response.not_sent
        assert helper_stand_in.calls() == []

    def test_true_when_the_helper_does_not_read_the_request(
        self, make_service, monkeypatch, service_module
    ):
        """The command lock is given back, so later commands are not stuck."""
        monkeypatch.setattr(command_helper, "HELPER_WRITE_TIMEOUT", 0.3)
        monkeypatch.setattr(command_helper, "HELPER_SCRIPT_PATH", str(HELPER_THAT_DOES_NOT_READ))
        service = make_service(command_helper=True)

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                operator_message=linuxcnc_pb2.OperatorMessageCommand(
                    type=linuxcnc_pb2.OperatorMessageCommand.TEXT,
                    message="x" * (4 * 1024 * 1024),
                )
            ),
            active_context(),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "could not be handed to the command helper process" in response.error_message
        assert response.not_sent
        assert service._outstanding_dispatch is None
        assert service._command_lock.acquire(timeout=THREAD_WAIT_TIMEOUT)
        service._command_lock.release()

    def test_true_when_linuxcnc_raised_in_the_helper_without_taking_a_message(
        self, helper_service, helper_stand_in
    ):
        helper_stand_in.set_behaviour("mdi", "raise_linuxcnc_error")

        response = helper_service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(mdi=linuxcnc_pb2.MdiCommand(command="G0 X1")),
            active_context(),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.not_sent

    def test_unset_when_a_helper_call_fails_with_bindings_that_have_no_serial(
        self, make_service, helper_stand_in
    ):
        helper_stand_in.set_behaviour("command", "no_serial")
        helper_stand_in.set_behaviour("mode", "raise_value_error")
        service = make_service(command_helper=True)

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert not response.not_sent

    def test_unset_when_a_command_call_fails_with_bindings_that_have_no_serial(
        self, service_module, make_service
    ):
        """Nothing shows whether the failed call had written its message."""
        with patch.object(
            service_module.linuxcnc, "command",
            side_effect=lambda: InProcessCommand(has_serial=False),
        ):
            service = make_service()
        service._command_channel.methods_that_raise["mode"] = RuntimeError("failed")

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert not response.not_sent

    def test_unset_when_a_command_of_several_messages_fails_part_way(self, make_service):
        service = make_service()
        service._command_channel.methods_that_raise["flood"] = RuntimeError("flood failed")

        response = service.SendCommand(coolant_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "flood failed"
        assert not response.not_sent
        # The message that did go out is what WaitComplete waits on now.
        assert service._latest_dispatch_record.linuxcnc_serial == 501

    def test_unset_when_the_bookkeeping_after_a_send_fails(self, make_service):
        """The command went out; whatever fails afterwards, it was sent."""
        service = make_service()

        with patch.object(
            service, "_record_dispatch", side_effect=RuntimeError("bookkeeping failed")
        ):
            response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "bookkeeping failed"
        assert not response.not_sent
        assert service._command_channel.calls == [("mode", (3,))]

    def test_unset_for_a_dispatched_command(self, make_service):
        service = make_service()

        response = service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not response.not_sent

    def test_unset_when_the_helper_is_lost_with_the_command(
        self, helper_service, helper_stand_in, worker_threads
    ):
        helper_stand_in.set_behaviour("mode", "block")
        send = worker_threads.submit(
            helper_service.SendCommand, mode_command(), active_context()
        )
        helper_stand_in.wait_until_entered()
        os.kill(helper_service._command_channel.process_id, signal.SIGKILL)

        response = send.result(timeout=THREAD_WAIT_TIMEOUT)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE
        assert not response.not_sent
        assert not response.HasField("linuxcnc_serial")


class TestHelperStartWhileWaitingForLinuxCNC:
    def test_failed_helper_starts_do_not_use_up_the_connect_attempts(
        self, service_module, make_service, helper_stand_in, monkeypatch
    ):
        """
        Only the helper's own process tried to connect, so the limit for a
        process that can no longer connect does not apply.
        """
        monkeypatch.setattr(service_module, "INITIAL_RECONNECT_INTERVAL", 0.001)
        monkeypatch.setattr(service_module, "MAX_WAIT_FOR_LINUXCNC_INTERVAL", 0.001)
        failed_starts_before_success = (
            service_module.MAX_CONNECT_ATTEMPTS_AFTER_GOOD_PROBE + 2
        )
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")
        start_attempts = []
        real_start = HelperCommandChannel.start

        def start_and_count(channel):
            start_attempts.append(channel)
            if len(start_attempts) > failed_starts_before_success:
                helper_stand_in.set_behaviour("stat.poll", "")
            real_start(channel)

        # Not reachable while the servicer is built, reachable afterwards.
        reachability_answers = iter([False])
        unrecoverable_reports = []
        connected = threading.Event()
        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            side_effect=lambda: next(reachability_answers, True),
        ), patch.object(HelperCommandChannel, "start", start_and_count):
            service = make_service(
                command_helper=True,
                require_linuxcnc=False,
                on_unrecoverable=unrecoverable_reports.append,
                on_connection_change=lambda is_connected: connected.set(),
            )
            service.start_waiting_for_linuxcnc()
            assert connected.wait(THREAD_WAIT_TIMEOUT)

        assert unrecoverable_reports == []
        assert len(start_attempts) == failed_starts_before_success + 1
        assert process_exists(service._command_channel.process_id)


    def test_helper_that_never_starts_is_reported_as_unrecoverable(
        self, service_module, make_service, helper_stand_in, monkeypatch
    ):
        monkeypatch.setattr(service_module, "INITIAL_RECONNECT_INTERVAL", 0.001)
        monkeypatch.setattr(service_module, "MAX_WAIT_FOR_LINUXCNC_INTERVAL", 0.001)
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")
        reachability_answers = iter([False])
        unrecoverable_reports = []
        reported = threading.Event()

        def report(text):
            unrecoverable_reports.append(text)
            reported.set()

        start_attempts = []
        real_start = HelperCommandChannel.start

        def start_and_count(channel):
            start_attempts.append(channel)
            real_start(channel)

        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            side_effect=lambda: next(reachability_answers, True),
        ), patch.object(HelperCommandChannel, "start", start_and_count):
            service = make_service(
                command_helper=True, require_linuxcnc=False, on_unrecoverable=report
            )
            service.start_waiting_for_linuxcnc()
            assert reported.wait(THREAD_WAIT_TIMEOUT)
            service._waiting_thread.join(THREAD_WAIT_TIMEOUT)

        assert len(start_attempts) == service_module.MAX_HELPER_START_FAILURES_AFTER_GOOD_PROBE
        assert len(unrecoverable_reports) == 1
        assert "command helper process could not be started" in unrecoverable_reports[0]
        assert "cannot send commands" in unrecoverable_reports[0]
        assert not service._connected


class TestConstruction:
    def test_helper_that_fails_after_a_good_probe_leaves_the_servicer_waiting(
        self, service_module, make_service, helper_stand_in
    ):
        """
        LinuxCNC went away between the probe and the helper's own check.
        Only the helper tried, so this process can wait and connect later.
        """
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")
        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            return_value=True,
        ):
            service = make_service(command_helper=True, require_linuxcnc=False)

        assert not service._connected
        assert service._command_channel is None

    def test_with_require_linuxcnc_the_failed_helper_start_still_raises(
        self, make_service, helper_stand_in
    ):
        helper_stand_in.set_behaviour("stat.poll", "raise_linuxcnc_error")

        with pytest.raises(CommandNotHandedOver):
            make_service(command_helper=True, require_linuxcnc=True)

    def test_failure_after_the_connect_ends_the_helper(self, service_module):
        """Nobody gets the half-built servicer, so it cleans up itself."""
        started_process_ids = []
        real_start = HelperCommandChannel.start

        def start_and_remember(channel):
            real_start(channel)
            started_process_ids.append(channel.process_id)

        with patch.object(HelperCommandChannel, "start", start_and_remember), \
                patch.object(
                    service_module.ErrorBroker, "start",
                    side_effect=RuntimeError("can't start new thread"),
                ):
            with pytest.raises(RuntimeError, match="can't start new thread"):
                service_module.LinuxCNCServiceServicer(
                    command_helper=True, error_channel_mode="always"
                )

        assert len(started_process_ids) == 1
        assert not process_exists(started_process_ids[0])


class TestHelperLoss:
    def test_unexpected_failure_while_waiting_for_the_reply_counts_as_a_loss(
        self, make_channel, helper_stand_in, worker_threads
    ):
        """Whatever goes wrong after the hand-over, the command may have been sent."""
        channel = make_channel()
        lost_process_id = channel.process_id
        channel.begin_command()

        with patch.object(
            command_helper._HelperProcess, "read_reply",
            side_effect=ValueError("filedescriptor out of range"),
        ):
            with pytest.raises(CommandHelperLost, match="may have been sent"):
                channel.mode(3)

        assert channel.helper_was_lost is True
        assert not process_exists(lost_process_id)
        # Nothing is left marked as in a call: close() can end the next helper.
        channel.restart_if_lost()
        restarted_process_id = channel.process_id
        channel.close()
        assert not process_exists(restarted_process_id)

    def test_helper_lost_with_a_command_is_replaced_at_once(
        self, make_service, helper_stand_in, worker_threads
    ):
        connection_changes = []
        service = make_service(
            command_helper=True, on_connection_change=connection_changes.append
        )
        lost_process_id = service._command_channel.process_id
        helper_stand_in.set_behaviour("mode", "block")
        send = worker_threads.submit(service.SendCommand, mode_command(), active_context())
        helper_stand_in.wait_until_entered()
        os.kill(lost_process_id, signal.SIGKILL)
        send.result(timeout=THREAD_WAIT_TIMEOUT)

        # Replaced before SendCommand returned, so the next command (which
        # may be a stop) does not wait for a start.
        new_process_id = service._command_channel.process_id
        assert new_process_id not in (None, lost_process_id)
        # The connection to LinuxCNC is what it was: health does not change.
        assert connection_changes == [True]
        assert service._connected

        response = service.SendCommand(feedrate_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.linuxcnc_serial == FIRST_STAND_IN_SERIAL
        assert helper_stand_in.process_ids() == [lost_process_id, new_process_id]

    def test_stop_behind_a_lost_command_does_not_wait_for_the_replacement(
        self, helper_service, helper_stand_in, worker_threads
    ):
        """
        The replacement is started after the command lock is given back. A
        stop queued meanwhile takes its turn and gets a helper of its own.
        """
        replacement_may_start = threading.Event()
        lock_held_during_restart = []
        real_restart_if_lost = HelperCommandChannel.restart_if_lost

        def restart_later(channel):
            # Only the first call, the lost command's, is held back; the
            # stop's own call afterwards goes through.
            is_first_call = not lock_held_during_restart
            lock_held_during_restart.append(helper_service._command_lock._is_owned())
            if is_first_call:
                replacement_may_start.wait(THREAD_WAIT_TIMEOUT)
            real_restart_if_lost(channel)

        helper_stand_in.set_behaviour("mode", "block")
        with patch.object(HelperCommandChannel, "restart_if_lost", restart_later):
            lost_send = worker_threads.submit(
                helper_service.SendCommand, mode_command(), active_context()
            )
            helper_stand_in.wait_until_entered()
            os.kill(helper_service._command_channel.process_id, signal.SIGKILL)

            stop = worker_threads.submit(
                helper_service.SendCommand, estop_command(), active_context()
            ).result(timeout=THREAD_WAIT_TIMEOUT)

            assert stop.status == linuxcnc_pb2.RCS_DONE
            assert not lost_send.done()  # still in its (held back) restart
            replacement_may_start.set()
            assert lost_send.result(timeout=THREAD_WAIT_TIMEOUT).error_message == (
                COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE
            )

        assert lock_held_during_restart[0] is False
        assert [name for name, _ in helper_stand_in.calls()] == ["mode", "state"]

    def test_serial_baseline_is_right_after_a_helper_was_replaced(
        self, helper_service, helper_stand_in
    ):
        """
        A new helper's command.serial starts at 0. A write LinuxCNC does
        not take through it must still be seen as not taken, and one it
        takes as taken.
        """
        assert helper_service.SendCommand(mode_command(), active_context()).status == (
            linuxcnc_pb2.RCS_DONE
        )
        os.kill(helper_service._command_channel.process_id, signal.SIGKILL)
        helper_service._command_channel._process._popen.wait(timeout=THREAD_WAIT_TIMEOUT)
        helper_stand_in.set_behaviour("feedrate", "write_fails")

        refused = helper_service.SendCommand(feedrate_command(), active_context())
        taken = helper_service.SendCommand(mode_command(), active_context())

        assert refused.status == linuxcnc_pb2.RCS_ERROR
        assert refused.not_sent
        assert taken.status == linuxcnc_pb2.RCS_DONE
        assert taken.linuxcnc_serial == FIRST_STAND_IN_SERIAL + 1

    def test_helper_that_died_while_idle_costs_no_command(
        self, helper_service, helper_stand_in
    ):
        lost_process_id = helper_service._command_channel.process_id
        os.kill(lost_process_id, signal.SIGKILL)
        helper_service._command_channel._process._popen.wait(timeout=THREAD_WAIT_TIMEOUT)

        response = helper_service.SendCommand(mode_command(), active_context())

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert helper_stand_in.calls() == [("mode", [3])]
        assert helper_stand_in.process_ids() != [lost_process_id]


class TestShutdown:
    def test_close_is_bounded_while_a_send_is_blocked_and_leaves_no_process(
        self, helper_service, helper_stand_in, worker_threads
    ):
        helper_stand_in.set_behaviour("mode", "block")
        helper_process_id = helper_service._command_channel.process_id
        send = worker_threads.submit(
            helper_service.SendCommand, mode_command(), active_context()
        )
        helper_stand_in.wait_until_entered()

        # Returns although the send, and with it the command lock, is stuck.
        worker_threads.submit(helper_service.close).result(timeout=THREAD_WAIT_TIMEOUT)

        assert not send.done()
        assert process_exists(helper_process_id)
        helper_stand_in.release()
        response = send.result(timeout=THREAD_WAIT_TIMEOUT)
        # The command that was under way is answered as usual.
        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.linuxcnc_serial == FIRST_STAND_IN_SERIAL
        assert not process_exists(helper_process_id)
        assert helper_service._command_channel.process_id is None

    def test_stop_flag_is_checked_after_the_helper_is_ready(
        self, helper_service, helper_stand_in, service_module
    ):
        """Starting a replacement helper takes time; close() can come meanwhile."""
        real_begin_command = HelperCommandChannel.begin_command

        def begin_command_and_meanwhile_stop(channel):
            real_begin_command(channel)
            helper_service._stop_requested.set()  # what close() does first

        with patch.object(
            HelperCommandChannel, "begin_command", begin_command_and_meanwhile_stop
        ):
            refused = helper_service.SendCommand(mode_command(), active_context())
            stop = helper_service.SendCommand(estop_command(), active_context())

        assert refused.error_message == service_module.SERVER_SHUTTING_DOWN_MESSAGE
        assert refused.not_sent
        assert stop.status == linuxcnc_pb2.RCS_DONE
        assert [method_name for method_name, _ in helper_stand_in.calls()] == ["state"]

    def test_close_ends_the_idle_helper(self, helper_service):
        helper_process_id = helper_service._command_channel.process_id

        helper_service.close()

        assert not process_exists(helper_process_id)

    def test_stop_is_still_sent_after_close_and_leaves_no_process(
        self, helper_service, helper_stand_in, service_module
    ):
        helper_service.close()

        refused = helper_service.SendCommand(mode_command(), active_context())
        stop = helper_service.SendCommand(estop_command(), active_context())

        assert refused.error_message == service_module.SERVER_SHUTTING_DOWN_MESSAGE
        assert refused.not_sent
        assert stop.status == linuxcnc_pb2.RCS_DONE
        assert [method_name for method_name, _ in helper_stand_in.calls()] == ["state"]
        assert not process_exists(helper_stand_in.process_ids()[0])
        assert helper_service._command_channel.process_id is None

    def test_rest_of_a_command_is_sent_when_close_comes_between_its_messages(
        self, helper_service, helper_stand_in, worker_threads
    ):
        helper_stand_in.set_behaviour("mist", "block")
        send = worker_threads.submit(
            helper_service.SendCommand, coolant_command(), active_context()
        )
        helper_stand_in.wait_until_entered()
        helper_service.close()
        helper_stand_in.release()

        response = send.result(timeout=THREAD_WAIT_TIMEOUT)

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert [method_name for method_name, _ in helper_stand_in.calls()] == ["mist", "flood"]
        assert response.linuxcnc_serial == FIRST_STAND_IN_SERIAL + 1
        assert not any(
            process_exists(process_id) for process_id in helper_stand_in.process_ids()
        )


class TestServerOption:
    """create_server sends through the helper unless told otherwise."""

    @pytest.fixture
    def server_module(self, service_module, mock_hal_module):
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            sys.modules.pop("linuxcnc_grpc.server", None)
            sys.modules.pop("linuxcnc_grpc.hal_service", None)
            server_module = importlib.import_module("linuxcnc_grpc.server")
            with patch.object(
                service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
                return_value=True,
            ):
                yield server_module
        sys.modules.pop("linuxcnc_grpc.server", None)
        sys.modules.pop("linuxcnc_grpc.hal_service", None)

    @staticmethod
    def _servicer_built_by(server_module, **create_server_arguments):
        built_servicers = []
        real_servicer_class = server_module.LinuxCNCServiceServicer

        def build_and_remember(**servicer_arguments):
            servicer = real_servicer_class(**servicer_arguments)
            built_servicers.append(servicer)
            return servicer

        with patch.object(server_module, "LinuxCNCServiceServicer", build_and_remember):
            server = server_module.create_server(
                host="127.0.0.1", port=0, error_channel_mode="off", **create_server_arguments
            )
        return server, built_servicers[0]

    def test_helper_is_the_default(self, server_module):
        server, servicer = self._servicer_built_by(server_module)
        try:
            server.start()
            assert isinstance(servicer._command_channel, HelperCommandChannel)
            helper_process_id = servicer._command_channel.process_id
            assert process_exists(helper_process_id)
        finally:
            server.stop(grace=None).wait(THREAD_WAIT_TIMEOUT)
        # Stopping the server ends the helper.
        assert not process_exists(helper_process_id)

    def test_opt_out_sends_in_process_and_starts_no_helper(
        self, server_module, helper_stand_in
    ):
        with patch.object(HelperCommandChannel, "start") as helper_start:
            server, servicer = self._servicer_built_by(server_module, command_helper=False)
        try:
            server.start()
            assert isinstance(servicer._command_channel, InProcessCommand)
            helper_start.assert_not_called()
        finally:
            server.stop(grace=None).wait(THREAD_WAIT_TIMEOUT)

    @pytest.mark.parametrize(
        "arguments, expected_command_helper",
        [([], True), (["--in-process-commands"], False)],
    )
    def test_command_line_flag(self, server_module, arguments, expected_command_helper):
        with patch.object(sys, "argv", ["linuxcnc-grpc", *arguments]), \
                patch.object(server_module, "serve") as serve:
            server_module.main()

        assert serve.call_args.kwargs["command_helper"] is expected_command_helper


class TestNotConnectedReason:
    """UNAVAILABLE for "not connected" carries trailing metadata saying so."""

    @pytest.fixture
    def stub_of_a_disconnected_server(self, service_module):
        with patch.object(
            service_module.LinuxCNCServiceServicer, "_linuxcnc_is_reachable",
            return_value=False,
        ):
            servicer = service_module.LinuxCNCServiceServicer(
                require_linuxcnc=False, error_channel_mode="off"
            )
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        linuxcnc_pb2_grpc.add_LinuxCNCServiceServicer_to_server(servicer, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        try:
            yield linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)
        finally:
            channel.close()
            server.stop(grace=None).wait(THREAD_WAIT_TIMEOUT)
            servicer.close()

    @pytest.mark.parametrize(
        "rpc_name, request_message",
        [
            ("GetStatus", linuxcnc_pb2.GetStatusRequest()),
            ("SendCommand", mode_command()),
            ("WaitComplete", linuxcnc_pb2.WaitCompleteRequest(timeout=1)),
            ("GetMachineConfig", linuxcnc_pb2.GetMachineConfigRequest()),
        ],
    )
    def test_unary_rpc(
        self, stub_of_a_disconnected_server, service_module, rpc_name, request_message
    ):
        with pytest.raises(grpc.RpcError) as rpc_error:
            getattr(stub_of_a_disconnected_server, rpc_name)(request_message, timeout=10)

        assert rpc_error.value.code() == grpc.StatusCode.UNAVAILABLE
        assert rpc_error.value.details() == (
            "LinuxCNC is not running, or the server has not connected to it yet"
        )
        assert (
            service_module.REASON_METADATA_KEY, service_module.REASON_NOT_CONNECTED
        ) in rpc_error.value.trailing_metadata()
        assert service_module.REASON_METADATA_KEY == "linuxcnc-grpc-reason"
        assert service_module.REASON_NOT_CONNECTED == "not-connected"

    def test_stream(self, stub_of_a_disconnected_server):
        with pytest.raises(grpc.RpcError) as rpc_error:
            list(stub_of_a_disconnected_server.StreamStatus(
                linuxcnc_pb2.StreamStatusRequest(), timeout=10
            ))

        assert rpc_error.value.code() == grpc.StatusCode.UNAVAILABLE
        assert ("linuxcnc-grpc-reason", "not-connected") in rpc_error.value.trailing_metadata()

    def test_other_failures_carry_no_reason(self, stub_of_a_disconnected_server):
        """The key marks one condition; an unrelated error must not have it."""
        with pytest.raises(grpc.RpcError) as rpc_error:
            stub_of_a_disconnected_server.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="../outside.ngc"), timeout=10
            )

        assert rpc_error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "linuxcnc-grpc-reason" not in dict(rpc_error.value.trailing_metadata())
