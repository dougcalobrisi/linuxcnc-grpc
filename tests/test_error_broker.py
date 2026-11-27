"""
Unit tests for the error broker, StreamErrors and LinuxCNCStatus.errors.
"""

import importlib
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import grpc
import pytest

from linuxcnc_grpc.error_broker import (
    ERROR_CHANNEL_ALWAYS,
    ERROR_CHANNEL_OFF,
    ERROR_CHANNEL_ON_DEMAND,
    ErrorBroker,
)

OPERATOR_ERROR_KIND = 11
OPERATOR_TEXT_KIND = 12


class FakeErrorChannel:
    """
    Stand-in for linuxcnc.error_channel(): a queue that poll() consumes.

    Counts polls, so tests can tell whether the server touched the channel.
    """

    def __init__(self):
        self._queued_messages = []
        self._queue_lock = threading.Lock()
        self.poll_count = 0

    def post(self, error_kind, message):
        with self._queue_lock:
            self._queued_messages.append((error_kind, message))

    def poll(self):
        with self._queue_lock:
            self.poll_count += 1
            return self._queued_messages.pop(0) if self._queued_messages else None

    @property
    def queued_count(self):
        with self._queue_lock:
            return len(self._queued_messages)


def wait_until(predicate, timeout=5.0):
    """Poll until predicate() is true; fail the test if it never is."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail("condition not reached in time")


class TestErrorBrokerBuffer:
    """Buffering and fan-out, driven synchronously with read_available()."""

    def test_messages_get_increasing_sequence_and_timestamps(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        channel.post(OPERATOR_ERROR_KIND, "first")
        channel.post(OPERATOR_TEXT_KIND, "second")
        time_before_read = time.time_ns()

        assert broker.read_available() == 2

        first_message, second_message = broker.recent(10)
        assert (first_message.sequence, first_message.error_kind, first_message.message) == (
            1, OPERATOR_ERROR_KIND, "first"
        )
        assert (second_message.sequence, second_message.message) == (2, "second")
        assert time_before_read <= first_message.timestamp_ns <= time.time_ns()
        assert second_message.timestamp_ns >= first_message.timestamp_ns

    def test_every_subscriber_receives_every_message(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        with broker.subscribe() as first_subscriber, broker.subscribe() as second_subscriber:
            channel.post(OPERATOR_ERROR_KIND, "one")
            channel.post(OPERATOR_ERROR_KIND, "two")
            broker.read_available()

            first_received = first_subscriber.wait_for_messages(timeout=0)
            second_received = second_subscriber.wait_for_messages(timeout=0)

        assert [m.message for m in first_received] == ["one", "two"]
        assert [m.message for m in second_received] == ["one", "two"]
        assert [m.sequence for m in first_received] == [m.sequence for m in second_received]

    def test_subscriber_receives_each_message_once(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        with broker.subscribe() as subscriber:
            channel.post(OPERATOR_ERROR_KIND, "one")
            broker.read_available()
            assert [m.message for m in subscriber.wait_for_messages(timeout=0)] == ["one"]
            assert subscriber.wait_for_messages(timeout=0) == []

            channel.post(OPERATOR_ERROR_KIND, "two")
            broker.read_available()
            assert [m.message for m in subscriber.wait_for_messages(timeout=0)] == ["two"]

    def test_new_subscriber_gets_only_new_messages_by_default(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        channel.post(OPERATOR_ERROR_KIND, "old")
        broker.read_available()

        with broker.subscribe() as late_subscriber:
            assert late_subscriber.wait_for_messages(timeout=0) == []
            channel.post(OPERATOR_ERROR_KIND, "new")
            broker.read_available()
            assert [m.message for m in late_subscriber.wait_for_messages(timeout=0)] == ["new"]

    def test_replay_buffered_starts_with_history(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        channel.post(OPERATOR_ERROR_KIND, "old-1")
        channel.post(OPERATOR_ERROR_KIND, "old-2")
        broker.read_available()

        with broker.subscribe(replay_buffered=True) as late_subscriber:
            channel.post(OPERATOR_ERROR_KIND, "new")
            broker.read_available()
            received = late_subscriber.wait_for_messages(timeout=0)

        assert [m.message for m in received] == ["old-1", "old-2", "new"]
        assert [m.sequence for m in received] == [1, 2, 3]

    def test_replay_buffered_with_empty_buffer(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)

        with broker.subscribe(replay_buffered=True) as subscriber:
            assert subscriber.wait_for_messages(timeout=0) == []
            channel.post(OPERATOR_ERROR_KIND, "first ever")
            broker.read_available()
            assert [m.message for m in subscriber.wait_for_messages(timeout=0)] == ["first ever"]

    def test_buffer_is_bounded_and_keeps_the_newest(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, buffer_size=3)
        for message_number in range(1, 6):
            channel.post(OPERATOR_ERROR_KIND, f"message {message_number}")
        broker.read_available()

        assert [m.message for m in broker.recent(10)] == ["message 3", "message 4", "message 5"]
        # Sequence numbers keep counting, so the gap is visible to clients.
        assert [m.sequence for m in broker.recent(10)] == [3, 4, 5]

    def test_slow_subscriber_loses_what_fell_out_of_the_buffer(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, buffer_size=2)
        with broker.subscribe() as slow_subscriber:
            for message_number in range(1, 5):
                channel.post(OPERATOR_ERROR_KIND, f"message {message_number}")
            broker.read_available()

            received = slow_subscriber.wait_for_messages(timeout=0)

        assert [m.sequence for m in received] == [3, 4]

    def test_recent_returns_the_newest_in_order(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        for message_number in range(1, 6):
            channel.post(OPERATOR_ERROR_KIND, f"message {message_number}")
        broker.read_available()

        assert [m.message for m in broker.recent(2)] == ["message 4", "message 5"]
        assert broker.recent(0) == []

    def test_read_available_stops_at_max_messages(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        for message_number in range(5):
            channel.post(OPERATOR_ERROR_KIND, f"message {message_number}")

        assert broker.read_available(max_messages=2) == 2
        assert channel.queued_count == 3

    def test_unexpected_poll_value_is_skipped(self):
        """Something that isn't a (kind, text) pair is not published."""
        polled_values = iter(["not a pair", None])
        broker = ErrorBroker(lambda: next(polled_values))

        assert broker.read_available() == 0
        assert broker.recent(10) == []

    def test_wait_for_messages_wakes_when_a_message_arrives(self):
        """A waiting subscriber is woken by the publish, well before its timeout."""
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll)
        received = []
        waiter_timeout = 30.0

        waiter_has_looked = threading.Event()
        real_messages_from = broker._messages_from

        def messages_from_reporting_the_first_look(first_sequence):
            waiter_has_looked.set()
            return real_messages_from(first_sequence)

        broker._messages_from = messages_from_reporting_the_first_look

        with broker.subscribe() as subscriber:
            waiter = threading.Thread(
                target=lambda: received.extend(
                    subscriber.wait_for_messages(timeout=waiter_timeout)
                )
            )
            waiter.start()
            # Make sure the waiter has found nothing and is blocked, so that
            # only a notification (not its first look at the buffer) can wake
            # it. It looks while holding the broker's condition and lets go
            # of it only by starting to wait, so once it has looked, being
            # able to take the condition here means it is waiting.
            assert waiter_has_looked.wait(5.0)
            with broker._condition:
                pass
            assert received == []
            channel.post(OPERATOR_ERROR_KIND, "wake up")
            broker.read_available()
            # Far shorter than the waiter's timeout: it must have been woken.
            waiter.join(timeout=5.0)
            still_waiting = waiter.is_alive()
            broker.stop()  # releases the waiter if the wakeup was lost
            waiter.join(timeout=5.0)

        assert not still_waiting
        assert [m.message for m in received] == ["wake up"]

    def test_rejects_unknown_mode_and_empty_buffer(self):
        with pytest.raises(ValueError, match="Unknown error channel mode"):
            ErrorBroker(lambda: None, mode="sometimes")
        with pytest.raises(ValueError, match="buffer_size"):
            ErrorBroker(lambda: None, buffer_size=0)


class TestErrorBrokerReader:
    """The reader thread and the three modes."""

    def test_on_demand_does_not_touch_the_channel_without_subscribers(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, mode=ERROR_CHANNEL_ON_DEMAND)
        broker.start()
        try:
            channel.post(OPERATOR_ERROR_KIND, "for the GUI")
            time.sleep(0.2)

            assert channel.poll_count == 0
            assert channel.queued_count == 1
            assert broker.recent(10) == []
        finally:
            broker.stop()

    def test_on_demand_reads_while_subscribed_and_stops_after(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, mode=ERROR_CHANNEL_ON_DEMAND)
        broker.start()
        try:
            with broker.subscribe() as subscriber:
                channel.post(OPERATOR_ERROR_KIND, "while subscribed")
                received = subscriber.wait_for_messages(timeout=5.0)
                assert [m.message for m in received] == ["while subscribed"]

            # Let a poll that was already under way finish, then check that
            # the reader has gone quiet.
            time.sleep(0.2)
            polls_after_unsubscribe = channel.poll_count
            channel.post(OPERATOR_ERROR_KIND, "for the GUI")
            time.sleep(0.2)

            assert channel.poll_count == polls_after_unsubscribe
            assert channel.queued_count == 1
        finally:
            broker.stop()

    def test_on_demand_stops_mid_batch_when_the_last_subscriber_leaves(self):
        """Messages still queued when the last subscriber leaves stay for the GUI."""
        broker = None
        queued_messages = [(OPERATOR_ERROR_KIND, f"message {n}") for n in range(1, 51)]
        subscription_holder = []

        def poll_and_unsubscribe_after_the_first():
            polled = queued_messages.pop(0) if queued_messages else None
            if subscription_holder:
                # The only subscriber disconnects while the first poll is under way.
                subscription_holder.pop().close()
            return polled

        broker = ErrorBroker(poll_and_unsubscribe_after_the_first, mode=ERROR_CHANNEL_ON_DEMAND)
        subscription_holder.append(broker.subscribe())
        broker.start()
        try:
            wait_until(lambda: len(broker.recent(100)) >= 1)
            time.sleep(0.2)

            assert len(broker.recent(100)) == 1
            assert len(queued_messages) == 49
        finally:
            broker.stop()

    def test_on_demand_keeps_reading_until_the_last_subscriber_leaves(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, mode=ERROR_CHANNEL_ON_DEMAND)
        broker.start()
        try:
            first_subscriber = broker.subscribe()
            second_subscriber = broker.subscribe()
            first_subscriber.close()
            first_subscriber.close()  # closing twice must not count twice
            assert broker.subscriber_count == 1

            channel.post(OPERATOR_ERROR_KIND, "still read")
            received = second_subscriber.wait_for_messages(timeout=5.0)
            assert [m.message for m in received] == ["still read"]
            second_subscriber.close()
            assert broker.subscriber_count == 0
        finally:
            broker.stop()

    def test_always_reads_without_subscribers(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, mode=ERROR_CHANNEL_ALWAYS)
        broker.start()
        try:
            channel.post(OPERATOR_ERROR_KIND, "buffered for later")
            wait_until(lambda: len(broker.recent(10)) == 1)

            with broker.subscribe(replay_buffered=True) as late_subscriber:
                received = late_subscriber.wait_for_messages(timeout=0)
            assert [m.message for m in received] == ["buffered for later"]
        finally:
            broker.stop()

    def test_off_never_reads(self):
        channel = FakeErrorChannel()
        broker = ErrorBroker(channel.poll, mode=ERROR_CHANNEL_OFF)
        broker.start()
        try:
            assert broker.reads_error_channel is False
            channel.post(OPERATOR_ERROR_KIND, "for the GUI")
            with broker.subscribe():
                time.sleep(0.2)
            assert channel.poll_count == 0
        finally:
            broker.stop()

    def test_reader_survives_a_failing_poll(self):
        """A poll that raises (LinuxCNC gone) is retried, not fatal."""
        channel = FakeErrorChannel()
        failures_left = [1]

        def poll_that_fails_once():
            if failures_left[0] > 0:
                failures_left[0] -= 1
                raise RuntimeError("LinuxCNC is not running")
            return channel.poll()

        broker = ErrorBroker(poll_that_fails_once, mode=ERROR_CHANNEL_ALWAYS)
        with patch("linuxcnc_grpc.error_broker.ERROR_POLL_FAILURE_BACKOFF", 0.01):
            broker.start()
            try:
                channel.post(OPERATOR_ERROR_KIND, "after recovery")
                wait_until(lambda: len(broker.recent(10)) == 1)
            finally:
                broker.stop()

    def test_stop_ends_the_reader_and_wakes_waiters(self):
        broker = ErrorBroker(lambda: None, mode=ERROR_CHANNEL_ALWAYS)
        broker.start()
        reader_thread = broker._reader_thread
        with broker.subscribe() as subscriber:
            waiter = threading.Thread(target=lambda: subscriber.wait_for_messages(timeout=30.0))
            waiter.start()
            broker.stop()
            waiter.join(timeout=5.0)

        assert not waiter.is_alive()
        assert not reader_thread.is_alive()


class ActiveUntil:
    """A context.is_active() that turns False once ``is_finished()`` is true."""

    def __init__(self, is_finished, timeout=5.0):
        self._is_finished = is_finished
        self._deadline = time.monotonic() + timeout

    def __call__(self):
        return not self._is_finished() and time.monotonic() < self._deadline


@pytest.fixture
def make_service(mock_linuxcnc_module, mock_linuxcnc_stat):
    """Build servicers on a FakeErrorChannel; stops their reader threads afterwards."""
    mock_linuxcnc_module.OPERATOR_ERROR = OPERATOR_ERROR_KIND
    mock_linuxcnc_module.OPERATOR_TEXT = OPERATOR_TEXT_KIND
    services = []

    from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

    def _make_service(error_channel, error_channel_mode=ERROR_CHANNEL_ON_DEMAND):
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = error_channel
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

            service = LinuxCNCServiceServicer(error_channel_mode=error_channel_mode)
        services.append(service)
        return service

    yield _make_service
    for service in services:
        service.close()


class TestStreamErrors:
    """Test StreamErrors RPC."""

    def test_yields_messages_with_type_sequence_and_timestamp(
        self, make_service, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel)
        channel.post(OPERATOR_ERROR_KIND, "Error message")
        channel.post(OPERATOR_TEXT_KIND, "Text message")
        results = []
        mock_grpc_context.is_active.side_effect = ActiveUntil(lambda: len(results) >= 2)

        for error_message in service.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context
        ):
            results.append(error_message)

        assert [r.message for r in results] == ["Error message", "Text message"]
        assert results[0].type == linuxcnc_pb2.ErrorMessage.OPERATOR_ERROR
        assert results[1].type == linuxcnc_pb2.ErrorMessage.OPERATOR_TEXT
        assert [r.sequence for r in results] == [1, 2]
        assert results[0].timestamp > 0

    def test_no_error_no_yield(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        service = make_service(FakeErrorChannel())
        mock_grpc_context.is_active.side_effect = [True, False]

        results = list(service.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context
        ))

        assert results == []

    def test_two_concurrent_streams_both_get_every_message(
        self, make_service, mock_grpc_context
    ):
        """The old behaviour split messages between streams; now each gets all of them."""
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel)
        received_by_stream = {"first": [], "second": []}

        def run_stream(stream_name):
            context = MagicMock()
            context.is_active.side_effect = ActiveUntil(
                lambda: len(received_by_stream[stream_name]) >= 3
            )
            for error_message in service.StreamErrors(
                linuxcnc_pb2.StreamErrorsRequest(), context
            ):
                received_by_stream[stream_name].append(error_message.message)

        stream_threads = [
            threading.Thread(target=run_stream, args=(stream_name,))
            for stream_name in received_by_stream
        ]
        for stream_thread in stream_threads:
            stream_thread.start()
        wait_until(lambda: service._error_broker.subscriber_count == 2)
        for message_number in range(1, 4):
            channel.post(OPERATOR_ERROR_KIND, f"message {message_number}")
        for stream_thread in stream_threads:
            stream_thread.join(timeout=5.0)

        expected_messages = ["message 1", "message 2", "message 3"]
        assert received_by_stream == {"first": expected_messages, "second": expected_messages}

    def test_replay_buffered_sends_history_first(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel, error_channel_mode=ERROR_CHANNEL_ALWAYS)
        channel.post(OPERATOR_ERROR_KIND, "before anyone subscribed")
        wait_until(lambda: len(service._error_broker.recent(10)) == 1)
        results = []
        mock_grpc_context.is_active.side_effect = ActiveUntil(lambda: len(results) >= 1)

        for error_message in service.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(replay_buffered=True), mock_grpc_context
        ):
            results.append(error_message)

        assert [r.message for r in results] == ["before anyone subscribed"]

    def test_without_replay_history_is_not_sent(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel, error_channel_mode=ERROR_CHANNEL_ALWAYS)
        channel.post(OPERATOR_ERROR_KIND, "before anyone subscribed")
        wait_until(lambda: len(service._error_broker.recent(10)) == 1)
        mock_grpc_context.is_active.side_effect = [True, False]

        results = list(service.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context
        ))

        assert results == []

    def test_on_demand_leaves_the_channel_alone_until_a_stream_starts(
        self, make_service, mock_grpc_context
    ):
        """The default mode takes nothing from the GUI while no client streams errors."""
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel)
        channel.post(OPERATOR_ERROR_KIND, "for the GUI")

        service.GetStatus(linuxcnc_pb2.GetStatusRequest(), mock_grpc_context)
        time.sleep(0.2)

        assert channel.poll_count == 0
        assert channel.queued_count == 1

    def test_stream_unsubscribes_when_the_client_goes_away(
        self, make_service, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        service = make_service(FakeErrorChannel())
        mock_grpc_context.is_active.side_effect = [True, False]

        list(service.StreamErrors(linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context))

        assert service._error_broker.subscriber_count == 0

    def test_stream_unsubscribes_when_the_generator_is_closed(
        self, make_service, mock_grpc_context
    ):
        """gRPC closes the generator when a client cancels mid-stream."""
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel)
        mock_grpc_context.is_active.return_value = True
        channel.post(OPERATOR_ERROR_KIND, "one")

        stream = service.StreamErrors(linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context)
        assert next(stream).message == "one"
        assert service._error_broker.subscriber_count == 1
        stream.close()

        assert service._error_broker.subscriber_count == 0

    def test_headers_are_sent_once_subscribed(self, make_service, mock_grpc_context):
        """Clients learn the subscription is live from the response headers."""
        from linuxcnc_pb import linuxcnc_pb2

        service = make_service(FakeErrorChannel())
        subscriber_counts_when_headers_sent = []
        mock_grpc_context.send_initial_metadata.side_effect = lambda metadata: (
            subscriber_counts_when_headers_sent.append(service._error_broker.subscriber_count)
        )
        mock_grpc_context.is_active.side_effect = [True, False]

        list(service.StreamErrors(linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context))

        assert subscriber_counts_when_headers_sent == [1]

    def test_stream_ends_when_the_broker_is_stopped(self, make_service, mock_grpc_context):
        """On server shutdown the stream returns instead of spinning on a stopped broker."""
        from linuxcnc_pb import linuxcnc_pb2

        service = make_service(FakeErrorChannel())
        mock_grpc_context.is_active.return_value = True  # the client is still connected
        results = []
        stream_thread = threading.Thread(target=lambda: results.extend(
            service.StreamErrors(linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context)
        ))
        stream_thread.start()
        wait_until(lambda: service._error_broker.subscriber_count == 1)

        service.close()
        stream_thread.join(timeout=5.0)

        assert not stream_thread.is_alive()
        assert service._error_broker.subscriber_count == 0

    def test_off_mode_refuses_the_stream(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel, error_channel_mode=ERROR_CHANNEL_OFF)
        channel.post(OPERATOR_ERROR_KIND, "for the GUI")

        results = list(service.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context
        ))

        assert results == []
        mock_grpc_context.abort.assert_called_once()
        assert mock_grpc_context.abort.call_args[0][0] == grpc.StatusCode.FAILED_PRECONDITION
        assert channel.poll_count == 0


class TestStatusErrors:
    """LinuxCNCStatus.errors is filled from the broker's buffer."""

    def test_status_carries_recent_errors(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel, error_channel_mode=ERROR_CHANNEL_ALWAYS)
        channel.post(OPERATOR_ERROR_KIND, "joint 0 following error")
        channel.post(OPERATOR_TEXT_KIND, "tool change")
        wait_until(lambda: len(service._error_broker.recent(10)) == 2)

        status = service.GetStatus(linuxcnc_pb2.GetStatusRequest(), mock_grpc_context)

        assert [e.message for e in status.errors] == ["joint 0 following error", "tool change"]
        assert [e.sequence for e in status.errors] == [1, 2]
        assert status.errors[0].type == linuxcnc_pb2.ErrorMessage.OPERATOR_ERROR
        assert status.errors[1].type == linuxcnc_pb2.ErrorMessage.OPERATOR_TEXT
        assert all(e.timestamp > 0 for e in status.errors)

    def test_status_errors_match_the_stream(self, make_service, mock_grpc_context):
        """The same message has the same sequence in status and in StreamErrors."""
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel)
        channel.post(OPERATOR_ERROR_KIND, "seen both ways")
        streamed = []
        mock_grpc_context.is_active.side_effect = ActiveUntil(lambda: len(streamed) >= 1)
        for error_message in service.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(), mock_grpc_context
        ):
            streamed.append(error_message)

        status = service.GetStatus(linuxcnc_pb2.GetStatusRequest(), MagicMock())

        assert len(status.errors) == 1
        assert status.errors[0] == streamed[0]

    def test_status_errors_are_capped_at_the_newest(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel, error_channel_mode=ERROR_CHANNEL_ALWAYS)
        # The service module is only importable while linuxcnc is mocked, so
        # read the constant from the module the servicer was built from.
        STATUS_ERRORS_LIMIT = service.GetStatus.__func__.__globals__["STATUS_ERRORS_LIMIT"]
        total_messages = STATUS_ERRORS_LIMIT + 5
        for message_number in range(1, total_messages + 1):
            channel.post(OPERATOR_ERROR_KIND, f"message {message_number}")
        wait_until(lambda: len(service._error_broker.recent(1000)) == total_messages)

        status = service.GetStatus(linuxcnc_pb2.GetStatusRequest(), mock_grpc_context)

        assert len(status.errors) == STATUS_ERRORS_LIMIT
        assert status.errors[0].message == "message 6"
        assert status.errors[-1].message == f"message {total_messages}"

    def test_status_errors_empty_when_nothing_was_read(self, make_service, mock_grpc_context):
        """On-demand without a stream, and off, read nothing: errors stays empty."""
        from linuxcnc_pb import linuxcnc_pb2

        for error_channel_mode in (ERROR_CHANNEL_ON_DEMAND, ERROR_CHANNEL_OFF):
            channel = FakeErrorChannel()
            service = make_service(channel, error_channel_mode=error_channel_mode)
            channel.post(OPERATOR_ERROR_KIND, "for the GUI")
            time.sleep(0.1)

            status = service.GetStatus(linuxcnc_pb2.GetStatusRequest(), mock_grpc_context)

            assert list(status.errors) == []
            assert channel.queued_count == 1

    def test_stream_status_carries_recent_errors(self, make_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        channel = FakeErrorChannel()
        service = make_service(channel, error_channel_mode=ERROR_CHANNEL_ALWAYS)
        channel.post(OPERATOR_ERROR_KIND, "in the stream")
        wait_until(lambda: len(service._error_broker.recent(10)) == 1)
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch("linuxcnc_grpc.linuxcnc_service.time.sleep"):
            statuses = list(service.StreamStatus(
                linuxcnc_pb2.StreamStatusRequest(interval_ms=10), mock_grpc_context
            ))

        assert [e.message for e in statuses[0].errors] == ["in the stream"]


class TestServerErrorChannelOption:
    """The --error-channel option reaches the servicer."""

    @pytest.mark.parametrize(
        "error_channel_mode", [ERROR_CHANNEL_ON_DEMAND, ERROR_CHANNEL_ALWAYS, ERROR_CHANNEL_OFF]
    )
    def test_create_server_passes_the_mode_on(self, error_channel_mode):
        mock_linuxcnc = MagicMock()
        mock_hal = MagicMock()
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc, "hal": mock_hal}):
            for module_name in (
                "linuxcnc_grpc.server",
                "linuxcnc_grpc.linuxcnc_service",
                "linuxcnc_grpc.hal_service",
            ):
                sys.modules.pop(module_name, None)
            server_module = importlib.import_module("linuxcnc_grpc.server")

            with patch.object(server_module, "LinuxCNCServiceServicer") as servicer_class, \
                    patch.object(server_module, "HalServiceServicer"), \
                    patch.object(server_module.grpc, "server"), \
                    patch.object(server_module.linuxcnc_pb2_grpc,
                                 "add_LinuxCNCServiceServicer_to_server"), \
                    patch.object(server_module.hal_pb2_grpc, "add_HalServiceServicer_to_server"), \
                    patch.object(server_module.health_pb2_grpc, "add_HealthServicer_to_server"):
                server_module.create_server(error_channel_mode=error_channel_mode)

            assert servicer_class.call_args.kwargs["error_channel_mode"] == error_channel_mode

    def test_command_line_default_is_on_demand(self):
        mock_linuxcnc = MagicMock()
        mock_hal = MagicMock()
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc, "hal": mock_hal}):
            for module_name in ("linuxcnc_grpc.server", "linuxcnc_grpc.linuxcnc_service",
                                "linuxcnc_grpc.hal_service"):
                sys.modules.pop(module_name, None)
            server_module = importlib.import_module("linuxcnc_grpc.server")

            with patch.object(server_module, "serve") as serve, \
                    patch.object(sys, "argv", ["linuxcnc-grpc"]):
                server_module.main()
            assert serve.call_args.kwargs["error_channel_mode"] == ERROR_CHANNEL_ON_DEMAND

            with patch.object(server_module, "serve") as serve, \
                    patch.object(sys, "argv", ["linuxcnc-grpc", "--error-channel", "always"]):
                server_module.main()
            assert serve.call_args.kwargs["error_channel_mode"] == ERROR_CHANNEL_ALWAYS


SERVER_MODULE_NAMES = (
    "linuxcnc_grpc.server",
    "linuxcnc_grpc.linuxcnc_service",
    "linuxcnc_grpc.hal_service",
)


def live_error_reader_threads():
    return {
        thread for thread in threading.enumerate()
        if thread.name == "linuxcnc-error-reader" and thread.is_alive()
    }


class TestServerStopsTheErrorReader:
    """A server made by create_server takes its error reader with it when it goes."""

    @pytest.fixture
    def error_channel(self):
        return FakeErrorChannel()

    @pytest.fixture
    def server_module(self, mock_linuxcnc_module, mock_linuxcnc_stat, error_channel):
        """
        The server module on a mocked linuxcnc and HAL, with a real gRPC server.

        The tests pass require_linuxcnc=True so that the servicer connects to
        the mocked linuxcnc directly instead of probing for a real one.
        """
        mock_linuxcnc_module.OPERATOR_ERROR = OPERATOR_ERROR_KIND
        mock_linuxcnc_module.OPERATOR_TEXT = OPERATOR_TEXT_KIND
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = error_channel
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module, "hal": MagicMock()}):
            for module_name in SERVER_MODULE_NAMES:
                sys.modules.pop(module_name, None)
            server_module = importlib.import_module("linuxcnc_grpc.server")

            with patch.object(server_module, "HalServiceServicer"), \
                    patch.object(server_module.hal_pb2_grpc, "add_HalServiceServicer_to_server"):
                yield server_module
        for module_name in SERVER_MODULE_NAMES:
            sys.modules.pop(module_name, None)

    @staticmethod
    def _create_always_reading_server(server_module):
        """Returns the server and the reader thread that creating it started."""
        readers_before = live_error_reader_threads()
        server = server_module.create_server(
            host="127.0.0.1", port=0, error_channel_mode=ERROR_CHANNEL_ALWAYS,
            require_linuxcnc=True,
        )
        new_readers = live_error_reader_threads() - readers_before
        assert len(new_readers) == 1
        return server, new_readers.pop()

    def test_stopping_the_server_stops_the_error_reader(self, server_module, error_channel):
        """A stopped server must not keep taking LinuxCNC's error messages."""
        server, reader_thread = self._create_always_reading_server(server_module)
        server.start()
        error_channel.post(OPERATOR_ERROR_KIND, "while the server runs")
        wait_until(lambda: error_channel.queued_count == 0)

        server.stop(grace=0)

        reader_thread.join(timeout=5.0)
        assert not reader_thread.is_alive()
        poll_count_when_stopped = error_channel.poll_count
        error_channel.post(OPERATOR_ERROR_KIND, "after the server stopped")
        time.sleep(0.1)
        assert error_channel.poll_count == poll_count_when_stopped
        assert error_channel.queued_count == 1

    def test_stopping_a_server_that_was_never_started_stops_the_error_reader(
        self, server_module
    ):
        server, reader_thread = self._create_always_reading_server(server_module)

        server.stop(grace=None)

        reader_thread.join(timeout=5.0)
        assert not reader_thread.is_alive()

    def test_stopping_twice_is_harmless(self, server_module):
        server, reader_thread = self._create_always_reading_server(server_module)
        server.start()

        first_stop_finished = server.stop(grace=0)
        second_stop_finished = server.stop(grace=0)

        assert first_stop_finished.wait(timeout=5.0)
        assert second_stop_finished.wait(timeout=5.0)
        reader_thread.join(timeout=5.0)
        assert not reader_thread.is_alive()

    def test_failed_hal_startup_leaves_no_error_reader(self, server_module):
        """If create_server fails part-way, no server exists to stop the reader later."""
        readers_before = live_error_reader_threads()
        readers_when_hal_failed = []

        def fail_to_access_hal():
            readers_when_hal_failed.extend(live_error_reader_threads() - readers_before)
            raise RuntimeError("Failed to access HAL")

        with patch.object(server_module, "HalServiceServicer", side_effect=fail_to_access_hal):
            with pytest.raises(RuntimeError, match="Failed to access HAL"):
                server_module.create_server(
                    host="127.0.0.1", port=0, error_channel_mode=ERROR_CHANNEL_ALWAYS,
                    require_linuxcnc=True,
                )

        # The reader was running by then, so the check below is not vacuous.
        assert len(readers_when_hal_failed) == 1
        readers_when_hal_failed[0].join(timeout=5.0)
        assert live_error_reader_threads() - readers_before == set()

    def test_failed_port_bind_leaves_no_error_reader(self, server_module):
        readers_before = live_error_reader_threads()

        with pytest.raises(Exception):
            server_module.create_server(
                host="256.256.256.256", port=50051, error_channel_mode=ERROR_CHANNEL_ALWAYS,
                require_linuxcnc=True,
            )

        wait_until(lambda: live_error_reader_threads() - readers_before == set())


class TestServicerClose:
    """close() is called by whoever stops the server, possibly more than once."""

    def test_close_twice_is_safe(self, make_service):
        service = make_service(FakeErrorChannel(), error_channel_mode=ERROR_CHANNEL_ALWAYS)
        reader_thread = service._error_broker._reader_thread
        assert reader_thread.is_alive()

        service.close()
        service.close()

        assert not reader_thread.is_alive()
