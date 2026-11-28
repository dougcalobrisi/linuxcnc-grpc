"""
Tests for the server's own health service (src/linuxcnc_grpc/health_service.py),
each against a real in-process gRPC server.
"""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent import futures

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from linuxcnc_grpc import health_service
from linuxcnc_grpc.health_service import (
    MAX_HEALTH_WATCHERS,
    HealthServicer,
    health_watcher_limit,
)

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
SERVICE_UNKNOWN = health_pb2.HealthCheckResponse.SERVICE_UNKNOWN
RPC_TIMEOUT = 10.0  # seconds; every call has a deadline, so no test can hang on one
STORM_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_watch_storm.py")
# The storm takes about a second when the server is fine. A server that
# freezes makes the child give up on its own after about 30 s; past this the
# child is killed.
STORM_HARD_TIMEOUT = 60.0  # seconds


def wait_until(predicate, timeout=5.0):
    """Poll until predicate() is true; fail the test if it never is."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@pytest.fixture
def start_health_server():
    """
    Start a real gRPC server with a health servicer; yields a function that
    returns (servicer, stub). Every server is stopped after the test.
    """
    servers_and_channels = []

    def _start(health_servicer=None, max_workers=10):
        if health_servicer is None:
            health_servicer = HealthServicer()
        grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, grpc_server)
        port = grpc_server.add_insecure_port("127.0.0.1:0")
        grpc_server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        servers_and_channels.append((grpc_server, channel))
        return health_servicer, health_pb2_grpc.HealthStub(channel)

    yield _start
    for grpc_server, channel in servers_and_channels:
        channel.close()
        assert grpc_server.stop(0).wait(RPC_TIMEOUT)


def check_status(stub, service_name=""):
    return stub.Check(
        health_pb2.HealthCheckRequest(service=service_name), timeout=RPC_TIMEOUT
    ).status


def open_watch(stub, service_name=""):
    return stub.Watch(health_pb2.HealthCheckRequest(service=service_name), timeout=RPC_TIMEOUT)


class TestHealthWatcherLimit:
    @pytest.mark.parametrize(
        "max_workers, expected_limit",
        [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (6, 3), (8, 4), (10, 4), (100, 4)],
    )
    def test_half_the_workers_at_most_four(self, max_workers, expected_limit):
        assert health_watcher_limit(max_workers) == expected_limit

    def test_default_limit_is_the_one_for_ten_workers(self):
        assert HealthServicer().max_concurrent_watchers == health_watcher_limit(10)
        assert MAX_HEALTH_WATCHERS == 4

    def test_a_negative_limit_is_refused(self):
        with pytest.raises(ValueError):
            HealthServicer(max_concurrent_watchers=-1)
        assert HealthServicer(max_concurrent_watchers=0).max_concurrent_watchers == 0

    def test_a_one_worker_server_refuses_watch_and_answers_check(self, start_health_server):
        """One watcher would hold the only worker; Check must still answer."""
        health_servicer, stub = start_health_server(
            HealthServicer(max_concurrent_watchers=health_watcher_limit(1)), max_workers=1
        )
        for _ in range(3):
            with pytest.raises(grpc.RpcError) as error_info:
                next(open_watch(stub))
            assert error_info.value.code() == grpc.StatusCode.UNIMPLEMENTED
            assert "not available" in error_info.value.details()
            assert "Check" in error_info.value.details()
            assert check_status(stub) == SERVING
        assert health_servicer.watcher_count() == 0


class TestCheck:
    def test_the_server_as_a_whole_starts_serving(self, start_health_server):
        _, stub = start_health_server()
        assert check_status(stub, "") == SERVING

    def test_check_reports_the_status_last_set(self, start_health_server):
        health_servicer, stub = start_health_server()
        health_servicer.set("linuxcnc.LinuxCNCService", NOT_SERVING)
        assert check_status(stub, "linuxcnc.LinuxCNCService") == NOT_SERVING
        health_servicer.set("linuxcnc.LinuxCNCService", SERVING)
        assert check_status(stub, "linuxcnc.LinuxCNCService") == SERVING

    def test_a_service_never_set_is_not_found(self, start_health_server):
        _, stub = start_health_server()
        with pytest.raises(grpc.RpcError) as error_info:
            check_status(stub, "no.such.Service")
        assert error_info.value.code() == grpc.StatusCode.NOT_FOUND


class TestWatch:
    def test_first_message_is_the_current_status(self, start_health_server):
        health_servicer, stub = start_health_server()
        health_servicer.set("", NOT_SERVING)
        updates = open_watch(stub)
        assert next(updates).status == NOT_SERVING
        updates.cancel()

    def test_a_service_not_set_yet_is_unknown_until_it_is_set(self, start_health_server):
        health_servicer, stub = start_health_server()
        updates = open_watch(stub, "hal.HalService")
        assert next(updates).status == SERVICE_UNKNOWN
        health_servicer.set("hal.HalService", SERVING)
        assert next(updates).status == SERVING
        updates.cancel()

    def test_each_change_is_sent_and_an_unchanged_status_is_not(self, start_health_server):
        health_servicer, stub = start_health_server()
        updates = open_watch(stub)
        assert next(updates).status == SERVING
        # Neither of these changes the watched status; the next message must
        # be the change that follows them.
        health_servicer.set("", SERVING)
        health_servicer.set("another.Service", NOT_SERVING)
        health_servicer.set("", NOT_SERVING)
        assert next(updates).status == NOT_SERVING
        health_servicer.set("", NOT_SERVING)
        health_servicer.set("", SERVING)
        assert next(updates).status == SERVING
        updates.cancel()

    def test_every_watcher_gets_the_change(self, start_health_server):
        health_servicer, stub = start_health_server()
        watchers = [open_watch(stub) for _ in range(health_servicer.max_concurrent_watchers)]
        for updates in watchers:
            assert next(updates).status == SERVING
        health_servicer.set("", NOT_SERVING)
        for updates in watchers:
            assert next(updates).status == NOT_SERVING
            updates.cancel()


class TestWatcherLimit:
    def test_a_watcher_over_the_limit_gets_unimplemented(self, start_health_server):
        health_servicer, stub = start_health_server(HealthServicer(max_concurrent_watchers=2))
        watchers = [open_watch(stub) for _ in range(2)]
        # A watcher's first message means it holds its slot.
        for updates in watchers:
            assert next(updates).status == SERVING
        assert health_servicer.watcher_count() == 2

        with pytest.raises(grpc.RpcError) as error_info:
            next(open_watch(stub))
        assert error_info.value.code() == grpc.StatusCode.UNIMPLEMENTED
        assert "limit of 2 concurrent health watchers" in error_info.value.details()
        assert "Check" in error_info.value.details()
        # The refused call took no slot, and Check still answers.
        assert health_servicer.watcher_count() == 2
        assert check_status(stub) == SERVING
        # The watchers already open still get changes.
        health_servicer.set("", NOT_SERVING)
        for updates in watchers:
            assert next(updates).status == NOT_SERVING
            updates.cancel()

    def test_a_freed_slot_can_be_used_at_once(self, start_health_server):
        health_servicer, stub = start_health_server(HealthServicer(max_concurrent_watchers=1))
        first_watcher = open_watch(stub)
        assert next(first_watcher).status == SERVING
        first_watcher.cancel()
        # The slot is given back when the server sees the cancel.
        wait_until(lambda: health_servicer.watcher_count() == 0)

        second_watcher = open_watch(stub)
        assert next(second_watcher).status == SERVING
        assert health_servicer.watcher_count() == 1
        second_watcher.cancel()

    def test_a_watcher_ending_at_its_deadline_frees_its_slot(self, start_health_server):
        health_servicer, stub = start_health_server(HealthServicer(max_concurrent_watchers=1))
        # Long enough for the first message on a loaded machine.
        short_watcher = stub.Watch(health_pb2.HealthCheckRequest(), timeout=5.0)
        assert next(short_watcher).status == SERVING
        with pytest.raises(grpc.RpcError) as error_info:
            next(short_watcher)
        assert error_info.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        wait_until(lambda: health_servicer.watcher_count() == 0)
        next_watcher = open_watch(stub)
        assert next(next_watcher).status == SERVING
        next_watcher.cancel()

    def test_no_watcher_is_left_after_clients_cancel(self, start_health_server):
        health_servicer, stub = start_health_server()
        watchers = [open_watch(stub) for _ in range(health_servicer.max_concurrent_watchers)]
        for updates in watchers:
            assert next(updates).status == SERVING
        for updates in watchers:
            updates.cancel()
        # Calls cancelled before or while their handler starts.
        for _ in range(50):
            open_watch(stub).cancel()
        wait_until(lambda: health_servicer.watcher_count() == 0)


class _CallAlreadyEndedContext:
    """A servicer context whose add_callback reports that the call has ended."""

    def __init__(self, real_context, add_callback_answers):
        self._real_context = real_context
        self._add_callback_answers = add_callback_answers

    def add_callback(self, callback):
        self._add_callback_answers.append(False)
        return False

    def __getattr__(self, name):
        return getattr(self._real_context, name)


class TestCallEndedBeforeTheHandlerRan:
    def test_nothing_is_registered_and_nothing_is_sent(self, start_health_server):
        """
        gRPC can run a Watch handler for a call that has already ended;
        add_callback then returns False. The servicer must not take a slot
        for it: nothing would ever give the slot back.
        """
        add_callback_answers = []
        slots_taken_during_the_call = []

        class EndedCallHealthServicer(HealthServicer):
            def Watch(self, request, context):
                ended_call_context = _CallAlreadyEndedContext(context, add_callback_answers)
                yield from super().Watch(request, ended_call_context)
                slots_taken_during_the_call.append(self.watcher_count())

        health_servicer, stub = start_health_server(
            EndedCallHealthServicer(max_concurrent_watchers=1)
        )
        assert list(open_watch(stub)) == []
        assert add_callback_answers == [False]
        assert slots_taken_during_the_call == [0]
        assert health_servicer.watcher_count() == 0


class TestSetNeverWaits:
    def test_set_and_graceful_shutdown_return_while_a_watcher_is_stuck_sending(
        self, start_health_server
    ):
        """
        The upstream servicer sent to every watcher from inside set(), under
        its lock, and froze when a send could not complete. Here a watcher
        is held just before its send, and set() and enter_graceful_shutdown()
        are called with another lock held, as the connection listener is
        called with the LinuxCNC servicer's state lock held.
        """
        send_allowed = threading.Event()
        send_allowed.set()
        waiting_to_send = threading.Event()

        class GatedHealthServicer(HealthServicer):
            def Watch(self, request, context):
                for response in super().Watch(request, context):
                    if not send_allowed.is_set():
                        waiting_to_send.set()
                    assert send_allowed.wait(RPC_TIMEOUT)
                    yield response

        health_servicer, stub = start_health_server(GatedHealthServicer())
        updates = open_watch(stub)
        assert next(updates).status == SERVING
        send_allowed.clear()
        health_servicer.set("", NOT_SERVING)
        assert waiting_to_send.wait(RPC_TIMEOUT)

        state_lock = threading.Lock()
        calls_returned = threading.Event()

        def set_with_the_state_lock_held():
            with state_lock:
                health_servicer.set("", SERVING)
                health_servicer.set("linuxcnc.LinuxCNCService", SERVING)
                health_servicer.enter_graceful_shutdown()
            calls_returned.set()

        setter = threading.Thread(target=set_with_the_state_lock_held, daemon=True)
        setter.start()
        try:
            assert calls_returned.wait(RPC_TIMEOUT), (
                "set() or enter_graceful_shutdown() blocked"
            )
            # Check answers while the watcher is stuck.
            assert check_status(stub, "") == NOT_SERVING
            assert check_status(stub, "linuxcnc.LinuxCNCService") == NOT_SERVING
        finally:
            send_allowed.set()
        # The held message goes out, the watcher is at the newest status, and
        # after the shutdown it ends its call.
        assert [response.status for response in updates] == [NOT_SERVING]
        setter.join(RPC_TIMEOUT)


class TestGracefulShutdown:
    def test_every_service_turns_not_serving_for_good(self, start_health_server):
        health_servicer, stub = start_health_server()
        health_servicer.set("linuxcnc.LinuxCNCService", SERVING)
        health_servicer.set("hal.HalService", SERVING)
        updates = open_watch(stub, "hal.HalService")
        assert next(updates).status == SERVING

        health_servicer.enter_graceful_shutdown()

        # NOT_SERVING, then the call ends with OK: nothing can change any more.
        assert [response.status for response in updates] == [NOT_SERVING]
        wait_until(lambda: health_servicer.watcher_count() == 0)
        for service_name in ("", "linuxcnc.LinuxCNCService", "hal.HalService"):
            assert check_status(stub, service_name) == NOT_SERVING
        # Later set() calls are ignored, and a second call changes nothing.
        health_servicer.set("hal.HalService", SERVING)
        health_servicer.set("new.Service", SERVING)
        health_servicer.enter_graceful_shutdown()
        assert check_status(stub, "hal.HalService") == NOT_SERVING
        with pytest.raises(grpc.RpcError) as error_info:
            check_status(stub, "new.Service")
        assert error_info.value.code() == grpc.StatusCode.NOT_FOUND
        # A watcher that starts now is told NOT_SERVING at once, and ends.
        late_watcher = open_watch(stub, "linuxcnc.LinuxCNCService")
        assert [response.status for response in late_watcher] == [NOT_SERVING]

    def test_watchers_end_so_a_graceful_stop_does_not_wait_for_them(self):
        """
        A watcher that never cancels used to hold its worker until gRPC
        cancelled it at the end of the grace period.
        """
        health_servicer = HealthServicer()
        grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, grpc_server)
        port = grpc_server.add_insecure_port("127.0.0.1:0")
        grpc_server.start()
        grace_period = 60.0
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            watchers = [open_watch(stub) for _ in range(health_servicer.max_concurrent_watchers)]
            for updates in watchers:
                assert next(updates).status == SERVING
            stop_started = time.monotonic()
            health_servicer.enter_graceful_shutdown()
            stopped = grpc_server.stop(grace_period)
            for updates in watchers:
                assert [response.status for response in updates] == [NOT_SERVING]
                assert updates.code() == grpc.StatusCode.OK
            assert stopped.wait(RPC_TIMEOUT)
            assert time.monotonic() - stop_started < RPC_TIMEOUT < grace_period


class _StandInContext:
    """The parts of a servicer context Watch uses, for calling it directly."""

    def __init__(self):
        self.callbacks = []

    def add_callback(self, callback):
        self.callbacks.append(callback)
        return True

    def is_active(self):
        return True

    def abort(self, code, details):
        raise AssertionError(f"unexpected abort: {code} {details}")


class TestWatchInternals:
    def test_a_change_between_the_read_and_the_wait_is_not_lost(
        self, start_health_server, monkeypatch
    ):
        """
        set() lands after the watcher has read the status and before it
        waits. The watcher clears its event before reading, so the wake-up
        survives and the change is sent at once. The periodic re-check is
        pushed out of reach, so a lost wake-up would show as a deadline.
        """
        monkeypatch.setattr(health_service, "WATCH_ACTIVITY_CHECK_INTERVAL", 3600.0)
        is_active_calls = []

        class ChangeAfterTheReadContext:
            def __init__(self, real_context, health_servicer):
                self._real_context = real_context
                self._health_servicer = health_servicer

            def is_active(self):
                # Watch asks this after reading the status, before it sends
                # and waits.
                is_active_calls.append(True)
                if len(is_active_calls) == 1:
                    self._health_servicer.set("", NOT_SERVING)
                return self._real_context.is_active()

            def __getattr__(self, name):
                return getattr(self._real_context, name)

        class ChangingHealthServicer(HealthServicer):
            def Watch(self, request, context):
                yield from super().Watch(request, ChangeAfterTheReadContext(context, self))

        _, stub = start_health_server(ChangingHealthServicer())
        updates = open_watch(stub)
        assert next(updates).status == SERVING
        assert next(updates).status == NOT_SERVING
        updates.cancel()

    def test_closing_the_generator_removes_the_watcher(self):
        health_servicer = HealthServicer()
        watch = health_servicer.Watch(health_pb2.HealthCheckRequest(), _StandInContext())
        assert next(watch).status == SERVING
        assert health_servicer.watcher_count() == 1
        watch.close()
        assert health_servicer.watcher_count() == 0

    def test_an_exception_in_the_generator_removes_the_watcher(self):
        health_servicer = HealthServicer()
        watch = health_servicer.Watch(health_pb2.HealthCheckRequest(), _StandInContext())
        assert next(watch).status == SERVING
        assert health_servicer.watcher_count() == 1
        with pytest.raises(RuntimeError, match="send failed"):
            watch.throw(RuntimeError("send failed"))
        assert health_servicer.watcher_count() == 0

    def test_a_failing_call_on_a_real_server_removes_the_watcher(self, start_health_server):
        class FailingContext:
            def __init__(self, real_context):
                self._real_context = real_context
                self._is_active_calls = 0

            def is_active(self):
                self._is_active_calls += 1
                if self._is_active_calls > 1:
                    raise RuntimeError("the call broke")
                return self._real_context.is_active()

            def __getattr__(self, name):
                return getattr(self._real_context, name)

        class FailingHealthServicer(HealthServicer):
            def Watch(self, request, context):
                yield from super().Watch(request, FailingContext(context))

        health_servicer, stub = start_health_server(FailingHealthServicer())
        updates = open_watch(stub)
        assert next(updates).status == SERVING
        health_servicer.set("", NOT_SERVING)
        with pytest.raises(grpc.RpcError) as error_info:
            next(updates)
        assert error_info.value.code() == grpc.StatusCode.UNKNOWN
        wait_until(lambda: health_servicer.watcher_count() == 0)


class TestWatchStorm:
    def test_clients_starting_and_cancelling_watch_do_not_freeze_the_server(self):
        """
        Eight threads start and cancel Watch while the status flips and the
        main thread checks every 50 attempts; afterwards a new client must
        get an answer, no watcher may be left and the server must stop. The
        upstream servicer froze in this storm within the first 50 attempts.
        Run in a child process with a hard timeout, so a server that freezes
        fails the test instead of hanging the suite.
        """
        try:
            storm = subprocess.run(
                [sys.executable, STORM_SCRIPT],
                capture_output=True,
                text=True,
                timeout=STORM_HARD_TIMEOUT,
                # The child imports what this process can import.
                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"the storm process did not finish within {STORM_HARD_TIMEOUT:g} s")
        result_lines = [
            line for line in storm.stdout.splitlines() if line.startswith("RESULT ")
        ]
        assert result_lines, f"the storm process printed no result: {storm.stderr[-2000:]}"
        result = json.loads(result_lines[-1][len("RESULT "):])

        assert result["failed_check"] is None, f"Check stopped answering: {result}"
        # The storm really ran: every attempt and Check, no thread failed, and
        # the server ran Watch handlers.
        assert result["thread_errors"] == [], result
        assert result["attempts_done"] == result["attempts"], result
        assert result["checks_answered"] == result["attempts"] // 50, result
        assert result["thread_watch_calls"] > 0, result
        assert result["status_flips"] > 0, result
        assert result["watch_handlers_run"] >= 50, result
        assert result["fresh_check"] in ("SERVING", "NOT_SERVING"), result
        assert result["watchers_left"] == 0, result
        assert result["stop_returned"], f"server.stop did not return: {result}"
        assert result["storm_threads_stuck"] == 0, result
