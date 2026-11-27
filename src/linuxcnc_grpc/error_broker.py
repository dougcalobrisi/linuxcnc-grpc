"""
Fan-out of LinuxCNC's error channel to any number of subscribers.

LinuxCNC's NML error channel is a single queue: linuxcnc.error_channel().poll()
removes the message it returns, and there is no way to peek. So at most one
reader in this server may poll it. ErrorBroker is that reader. It stamps each
message with a sequence number and a timestamp, keeps the most recent ones in a
bounded buffer, and hands every message to every subscriber.

The broker does not make reading non-destructive towards other processes: a
message the server reads is still gone for the machine's GUI. The mode decides
whether, and when, the server reads at all.
"""

import logging
import threading
import time
from collections import deque
from typing import Callable, NamedTuple

logger = logging.getLogger(__name__)

# The server reads the error channel only while at least one StreamErrors
# subscriber is connected. Nothing is taken from the GUI otherwise. Default.
ERROR_CHANNEL_ON_DEMAND = "on-demand"
# The server reads the error channel from startup. The GUI and the server
# then race for each message: whichever reads it first takes it.
ERROR_CHANNEL_ALWAYS = "always"
# The server never reads the error channel. StreamErrors is refused.
ERROR_CHANNEL_OFF = "off"
ERROR_CHANNEL_MODES = (ERROR_CHANNEL_ON_DEMAND, ERROR_CHANNEL_ALWAYS, ERROR_CHANNEL_OFF)

DEFAULT_ERROR_BUFFER_SIZE = 256  # messages kept for replay and for status
ERROR_POLL_INTERVAL = 0.05  # seconds between polls of an empty channel
ERROR_POLL_FAILURE_BACKOFF = 1.0  # seconds to wait after a failed poll


class BufferedError(NamedTuple):
    """
    One message read from LinuxCNC's error channel.

    Attributes:
        sequence: Position in the order the server read messages, starting at
            1 and never reused while the server runs.
        error_kind: The linuxcnc kind constant (linuxcnc.OPERATOR_ERROR, ...).
        message: The message text.
        timestamp_ns: When the server read it, unix time in nanoseconds.
    """

    sequence: int
    error_kind: int
    message: str
    timestamp_ns: int


class ErrorSubscription:
    """
    One subscriber's position in the stream of messages.

    Use as a context manager, so the broker knows when the subscriber leaves:

        with broker.subscribe() as subscription:
            new_messages = subscription.wait_for_messages(timeout=0.25)
    """

    def __init__(self, broker: "ErrorBroker", next_sequence: int):
        self._broker = broker
        self._next_sequence = next_sequence
        self._closed = False

    def __enter__(self) -> "ErrorSubscription":
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        self.close()

    def wait_for_messages(self, timeout: float) -> list[BufferedError]:
        """
        Return the messages this subscriber has not seen yet, in order.

        Blocks for up to ``timeout`` seconds if there are none, and returns an
        empty list if none arrive. A subscriber that falls further behind
        than the buffer is long loses the messages that dropped out of it.
        """
        new_messages = self._broker._wait_for_messages_from(self._next_sequence, timeout)
        if new_messages:
            self._next_sequence = new_messages[-1].sequence + 1
        return new_messages

    def close(self) -> None:
        """Leave the broker. Safe to call more than once."""
        if not self._closed:
            self._closed = True
            self._broker._remove_subscriber()


class ErrorBroker:
    """
    Reads LinuxCNC's error channel in one place and fans messages out.

    Args:
        poll_error_channel: Called to read one message. Returns
            ``(error_kind, message)`` or None when the channel is empty, like
            ``linuxcnc.error_channel().poll()``. It may raise; the broker
            logs, backs off and tries again.
        mode: One of ERROR_CHANNEL_MODES.
        buffer_size: How many recent messages to keep.
    """

    def __init__(
        self,
        poll_error_channel: Callable[[], "tuple[int, str] | None"],
        mode: str = ERROR_CHANNEL_ON_DEMAND,
        buffer_size: int = DEFAULT_ERROR_BUFFER_SIZE,
    ):
        if mode not in ERROR_CHANNEL_MODES:
            raise ValueError(
                f"Unknown error channel mode {mode!r}; expected one of {ERROR_CHANNEL_MODES}"
            )
        if buffer_size < 1:
            raise ValueError("buffer_size must be at least 1")
        self._poll_error_channel = poll_error_channel
        self._mode = mode
        # Guards everything below, and wakes the reader and the subscribers.
        self._condition = threading.Condition()
        self._buffer: deque[BufferedError] = deque(maxlen=buffer_size)
        self._next_sequence = 1
        self._subscriber_count = 0
        self._stopped = False
        self._reader_thread: threading.Thread | None = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_stopped(self) -> bool:
        """True once stop() has been called; no more messages will arrive."""
        with self._condition:
            return self._stopped

    @property
    def reads_error_channel(self) -> bool:
        """False when the server is configured never to read the channel."""
        return self._mode != ERROR_CHANNEL_OFF

    # ------------------------------------------------------------------
    # Reader
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the reader thread. Does nothing in "off" mode or if already started."""
        with self._condition:
            if not self.reads_error_channel or self._reader_thread is not None or self._stopped:
                return
            self._reader_thread = threading.Thread(
                target=self._run_reader, name="linuxcnc-error-reader", daemon=True
            )
            self._reader_thread.start()

    def stop(self) -> None:
        """Stop the reader thread and wake every waiting subscriber."""
        with self._condition:
            self._stopped = True
            reader_thread = self._reader_thread
            self._condition.notify_all()
        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=2.0)

    def _should_read(self) -> bool:
        """Whether the reader may poll right now. Caller holds self._condition."""
        if self._mode == ERROR_CHANNEL_ALWAYS:
            return True
        if self._mode == ERROR_CHANNEL_ON_DEMAND:
            return self._subscriber_count > 0
        return False

    def _run_reader(self) -> None:
        """Reader thread: poll while allowed, sleep while the channel is empty."""
        while True:
            with self._condition:
                while not self._stopped and not self._should_read():
                    self._condition.wait()
                if self._stopped:
                    return
            try:
                messages_read = self.read_available(only_while_allowed=True)
            except Exception as e:
                logger.warning(f"Reading the LinuxCNC error channel failed: {e}")
                self._sleep_unless_stopped(ERROR_POLL_FAILURE_BACKOFF)
                continue
            if messages_read == 0:
                self._sleep_unless_stopped(ERROR_POLL_INTERVAL)

    def _sleep_unless_stopped(self, seconds: float) -> None:
        with self._condition:
            if not self._stopped:
                self._condition.wait_for(lambda: self._stopped, timeout=seconds)

    def _may_read_now(self) -> bool:
        with self._condition:
            return not self._stopped and self._should_read()

    def read_available(self, max_messages: int = 100, only_while_allowed: bool = False) -> int:
        """
        Read what is waiting in the error channel into the buffer.

        Stops at an empty channel or after ``max_messages``, so a flood of
        messages cannot keep the reader from noticing a stop. Returns how
        many messages were read. Exceptions from the poll propagate.

        Args:
            max_messages: Most messages to read in one call.
            only_while_allowed: Check the mode before every single poll and
                stop as soon as reading is no longer allowed. The reader
                thread sets this, so that in on-demand mode it stops taking
                messages the moment the last subscriber leaves instead of
                draining the rest of a batch. Without it the call reads
                regardless of mode.
        """
        messages_read = 0
        while messages_read < max_messages:
            if only_while_allowed and not self._may_read_now():
                break
            polled = self._poll_error_channel()
            if not polled:
                break
            try:
                error_kind, message = polled
            except (TypeError, ValueError):
                logger.error(f"Unexpected value from the error channel: {polled!r}")
                break
            self._publish(error_kind, str(message))
            messages_read += 1
        return messages_read

    def _publish(self, error_kind: int, message: str) -> None:
        with self._condition:
            self._buffer.append(BufferedError(
                sequence=self._next_sequence,
                error_kind=error_kind,
                message=message,
                timestamp_ns=time.time_ns(),
            ))
            self._next_sequence += 1
            self._condition.notify_all()

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------

    def subscribe(self, replay_buffered: bool = False) -> ErrorSubscription:
        """
        Add a subscriber.

        Args:
            replay_buffered: If True the subscriber first receives the
                messages already in the buffer, oldest first. If False it
                receives only messages read from now on.
        """
        with self._condition:
            if replay_buffered and self._buffer:
                next_sequence = self._buffer[0].sequence
            else:
                next_sequence = self._next_sequence
            self._subscriber_count += 1
            # Wakes the reader in on-demand mode.
            self._condition.notify_all()
            return ErrorSubscription(self, next_sequence)

    def _remove_subscriber(self) -> None:
        with self._condition:
            self._subscriber_count -= 1

    @property
    def subscriber_count(self) -> int:
        with self._condition:
            return self._subscriber_count

    def _messages_from(self, first_sequence: int) -> list[BufferedError]:
        """Buffered messages with sequence >= first_sequence. Caller holds self._condition."""
        if not self._buffer or self._buffer[-1].sequence < first_sequence:
            return []
        return [
            buffered_error for buffered_error in self._buffer
            if buffered_error.sequence >= first_sequence
        ]

    def _wait_for_messages_from(self, first_sequence: int, timeout: float) -> list[BufferedError]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._stopped or bool(self._messages_from(first_sequence)),
                timeout=timeout,
            )
            return self._messages_from(first_sequence)

    def recent(self, limit: int) -> list[BufferedError]:
        """The newest ``limit`` buffered messages, oldest first."""
        if limit <= 0:
            return []
        with self._condition:
            return list(self._buffer)[-limit:]
