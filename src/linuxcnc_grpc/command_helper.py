"""
Sending LinuxCNC commands through a helper process.

A ``linuxcnc.command()`` method writes its message to LinuxCNC's command
queue and then waits up to 5 seconds for the task to echo it, without
releasing the interpreter lock. With a task that does not answer, a send
made in the server's own process therefore stalls every thread of the
server. Here the send is made by a child process
(command_helper_process.py) and the server's thread waits on a pipe, which
releases the interpreter lock.

``HelperCommandChannel`` is what the servicer's handlers call in place of a
``linuxcnc.command()``: ``channel.mode(3)`` is forwarded to the helper and
returns, or raises, what the call did there.

This module does not import ``linuxcnc``; the exception type to raise for a
``linuxcnc.error`` in the helper is passed in.
"""

import builtins
import json
import logging
import math
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# The helper's program, run as a script with the server's own interpreter.
HELPER_SCRIPT_PATH = str(Path(__file__).with_name("command_helper_process.py"))
# Longest wait for the helper's first line. Starting took 14 to 23 ms on
# the simulator; this only bounds a helper that hangs. A command (a stop
# included) that needs a new helper can wait this long for it.
HELPER_START_TIMEOUT = 5.0  # seconds
# Longest wait for one reply. LinuxCNC gives up on a message after 5 seconds
# and no command method sends more than two, so a helper that has not
# answered by now is not in a send any more.
HELPER_REPLY_TIMEOUT = 30.0  # seconds
# Longest wait for one request to be written to the helper. The helper is
# idle and reading whenever a request is written (commands go one at a
# time), so this only bounds a helper that has stopped reading; without it
# a request larger than the pipe buffer would block for ever, holding the
# command lock.
HELPER_WRITE_TIMEOUT = 5.0  # seconds
# How long an idle helper gets to end after its pipe was closed.
HELPER_EXIT_WAIT = 0.5  # seconds

COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE = (
    "The command helper process stopped while it had the command; the "
    "command may have been sent to LinuxCNC. Check the status before "
    "sending it again"
)


class CommandNotHandedOver(RuntimeError):
    """The request did not reach a helper, so nothing was sent to LinuxCNC."""


class CommandHelperLost(RuntimeError):
    """
    The helper died, or its pipe broke, after it was handed a request.

    The helper writes to LinuxCNC before it answers, so a missing answer
    does not mean the command was not sent.
    """


class _HelperProcess:
    """One helper process and the two pipes to it."""

    def __init__(self, helper_argv: list[str]):
        # The server's interpreter and environment, as the reachability
        # check uses (a child without HOME could not reach LinuxCNC). A
        # session of its own, so a Ctrl+C in the server's terminal does not
        # reach it. Unbuffered pipes: every request is one write.
        self._popen = subprocess.Popen(
            helper_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
        # Writes must be able to give up (see write_request).
        os.set_blocking(self._popen.stdin.fileno(), False)
        self._unread_reply_bytes = bytearray()
        self.process_id = self._popen.pid
        # command.serial in the helper after its latest call (None if the
        # bindings have none).
        self.command_serial: int | None = None

    def is_running(self) -> bool:
        return self._popen.poll() is None

    def write_request(self, request_line: bytes, timeout: float) -> None:
        """
        Write one request line, giving up after ``timeout`` seconds.

        Raises OSError if the pipe is broken, TimeoutError (an OSError) if
        the helper does not take the whole line in time. The line ends with
        its only newline, and os.write() reports exactly what it wrote, so
        when this raises, the newline has not been written and the helper,
        which acts only on a complete line, cannot act on the request.
        """
        request_descriptor = self._popen.stdin.fileno()
        writability_poller = select.poll()
        writability_poller.register(request_descriptor, select.POLLOUT)
        deadline = time.monotonic() + timeout
        bytes_written = 0
        while bytes_written < len(request_line):
            # Checked before every write but the first, also after a write
            # that made progress: a helper that takes a little at a time
            # must not keep this past the deadline. Once the whole line is
            # written the loop ends, and the request is handed over.
            seconds_remaining = deadline - time.monotonic()
            if bytes_written > 0 and seconds_remaining <= 0:
                raise TimeoutError(
                    f"the helper took {bytes_written} of {len(request_line)} bytes "
                    f"in {timeout:g} s"
                )
            try:
                bytes_written += os.write(request_descriptor, request_line[bytes_written:])
                continue
            except BlockingIOError:
                pass
            if seconds_remaining <= 0:
                raise TimeoutError(
                    f"the helper took {bytes_written} of {len(request_line)} bytes "
                    f"in {timeout:g} s"
                )
            writability_poller.poll(math.ceil(seconds_remaining * 1000))

    def read_reply(self, timeout: float) -> dict:
        """
        Read one reply line, waiting at most ``timeout`` seconds.

        The wait is a poll() on the pipe, which releases the interpreter
        lock. (Not select(): it fails for a descriptor numbered 1024 or
        more, which a helper started in a busy server can get.) Raises
        CommandHelperLost if the helper ended, sent something that is not a
        reply, or did not answer in time.
        """
        reply_descriptor = self._popen.stdout.fileno()
        reply_poller = select.poll()
        reply_poller.register(reply_descriptor, select.POLLIN)
        deadline = time.monotonic() + timeout
        while b"\n" not in self._unread_reply_bytes:
            seconds_remaining = deadline - time.monotonic()
            if seconds_remaining <= 0:
                raise CommandHelperLost("no reply in time")
            # poll() takes milliseconds; rounded up so a short remainder
            # is not a busy loop of zero-length waits.
            if not reply_poller.poll(math.ceil(seconds_remaining * 1000)):
                continue
            try:
                chunk = os.read(reply_descriptor, 65536)
            except OSError as read_error:
                raise CommandHelperLost(f"reading the reply failed: {read_error}") from None
            if not chunk:
                raise CommandHelperLost("the helper process ended")
            self._unread_reply_bytes.extend(chunk)
        reply_line, _, remaining_bytes = bytes(self._unread_reply_bytes).partition(b"\n")
        self._unread_reply_bytes = bytearray(remaining_bytes)
        try:
            reply = json.loads(reply_line)
        except ValueError:
            raise CommandHelperLost("the helper sent something that is not a reply") from None
        if not isinstance(reply, dict):
            raise CommandHelperLost("the helper sent something that is not a reply")
        return reply

    def end(self, kill_now: bool = False) -> None:
        """
        End the process and collect its exit status.

        Closes the request pipe, which is how the helper is told to end.
        Call only for a helper that is not in a call, or (``kill_now``) for
        one that has failed: a helper that does not end by itself is killed,
        and one in the middle of a send to LinuxCNC must not be.
        """
        for pipe in (self._popen.stdin, self._popen.stdout):
            try:
                pipe.close()
            except OSError:
                pass
        if not kill_now:
            try:
                self._popen.wait(timeout=HELPER_EXIT_WAIT)
                return
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"Command helper (pid {self.process_id}) did not end after its "
                    "pipe was closed; killing it"
                )
        if self._popen.poll() is None:
            self._popen.kill()
        try:
            self._popen.wait(timeout=HELPER_EXIT_WAIT)
        except subprocess.TimeoutExpired:
            logger.error(f"Command helper (pid {self.process_id}) could not be ended")


class HelperCommandChannel:
    """
    Stands in for ``linuxcnc.command()``; every call is made by the helper.

    One thread at a time may send (the servicer's command lock sees to
    that). ``close()`` may be called from any thread at any time.

    ``serial`` is the serial of the last message LinuxCNC took through this
    channel, like ``linuxcnc.command().serial``: it changes when a write to
    LinuxCNC succeeded and stays when the write failed. Replacing the helper
    process does not change it. It is None with bindings that have no
    serial.

    A helper that is gone is replaced: by ``begin_command()`` before a
    command, and by ``restart_if_lost()`` after one. Starting a helper is
    safe while LinuxCNC may be down, because the helper makes the
    connection check itself and a failed check costs only that process.
    After ``close()`` a helper is started for each call that still arrives
    (a stop, or the rest of a command that had begun) and ended after it.
    """

    def __init__(
        self,
        linuxcnc_error_type: type = RuntimeError,
        helper_argv: list[str] | None = None,
    ):
        self._linuxcnc_error_type = linuxcnc_error_type
        self._helper_argv = (
            list(helper_argv) if helper_argv is not None
            else [sys.executable, HELPER_SCRIPT_PATH]
        )
        self._process: _HelperProcess | None = None
        self._serial: int | None = None
        self._next_request_id = 1
        self._closed = False
        self._call_in_flight = False
        # Guards _process, _call_in_flight and the request pipe. Never held
        # while waiting for a reply.
        self._process_lock = threading.Lock()
        # Kept for the command in progress; reset by begin_command().
        self.requests_handed_over = 0
        self.helper_was_lost = False

    @property
    def serial(self) -> int | None:
        return self._serial

    @property
    def process_id(self) -> int | None:
        """The running helper's process id, or None."""
        process = self._process
        return process.process_id if process is not None else None

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        """
        Start the first helper. Raises CommandNotHandedOver if it cannot be
        started or cannot reach LinuxCNC.
        """
        with self._process_lock:
            self._start_process()
            self._serial = self._process.command_serial

    def _start_process(self) -> None:
        """Start a helper and wait until it is ready. Caller holds _process_lock."""
        started = time.monotonic()
        try:
            process = _HelperProcess(self._helper_argv)
        except OSError as spawn_error:
            raise CommandNotHandedOver(
                f"The command helper process could not be started: {spawn_error}"
            ) from None
        try:
            ready_message = process.read_reply(HELPER_START_TIMEOUT)
        except CommandHelperLost as start_error:
            process.end(kill_now=True)
            raise CommandNotHandedOver(
                f"The command helper process did not start: {start_error}"
            ) from None
        if ready_message.get("ready") is not True:
            process.end(kill_now=True)
            raise CommandNotHandedOver(
                "The command helper process could not connect to LinuxCNC: "
                f"{ready_message.get('error')}"
            )
        process.command_serial = _plain_int_or_none(ready_message.get("serial"))
        self._process = process
        logger.info(
            f"Command helper started (pid {process.process_id}) in "
            f"{(time.monotonic() - started) * 1000:.0f} ms"
        )

    def _process_ready_for_a_call(self) -> _HelperProcess:
        """
        The running helper, started first if there is none.

        Caller holds _process_lock. Raises CommandNotHandedOver.
        """
        process = self._process
        if process is not None and process.is_running():
            return process
        if process is not None:
            logger.warning(
                f"Command helper (pid {process.process_id}) has ended; starting another"
            )
            self._process = None
            process.end(kill_now=True)
        self._start_process()
        return self._process

    def begin_command(self) -> None:
        """
        Prepare for one command: have a helper running, forget the counts
        of the previous command.

        Raises CommandNotHandedOver if there is no helper and none can be
        started. After close() nothing is started here; a call that does
        arrive starts its own.
        """
        self.requests_handed_over = 0
        self.helper_was_lost = False
        if self._closed:
            return
        with self._process_lock:
            self._process_ready_for_a_call()
            if self._closed:
                # close() came during the start and left the helper to us.
                self._end_idle_process()

    def restart_if_lost(self) -> None:
        """
        Start a new helper if the last command lost its one, so the next
        command (which may be a stop) does not have to wait for the start.
        A failure is logged; begin_command() tries again.
        """
        if self._closed:
            return
        with self._process_lock:
            process = self._process
            if process is not None and process.is_running():
                return
            try:
                self._process_ready_for_a_call()
            except CommandNotHandedOver as start_error:
                logger.error(f"The command helper could not be restarted: {start_error}")
                return
            if self._closed:
                self._end_idle_process()

    def close(self) -> None:
        """
        End the helper. Returns within about twice HELPER_EXIT_WAIT.

        A helper that is in a call is left alone: its command is already on
        its way to LinuxCNC, and the thread waiting for the reply ends the
        helper afterwards. If that thread never returns, the helper still
        ends when this process does, because its pipe closes then.
        """
        self._closed = True
        if not self._process_lock.acquire(timeout=HELPER_EXIT_WAIT):
            # A helper is being started or written to; that call sees the
            # flag and ends the helper when it is done.
            return
        try:
            if self._call_in_flight:
                return
            self._end_idle_process()
        finally:
            self._process_lock.release()

    def _end_idle_process(self) -> None:
        """End the helper, which is not in a call. Caller holds _process_lock."""
        process = self._process
        self._process = None
        if process is not None:
            process.end()

    def _forward(self, method_name: str, arguments: tuple) -> None:
        """Have the helper call ``command.<method_name>(*arguments)``."""
        with self._process_lock:
            process = self._process_ready_for_a_call()
            request_id = self._next_request_id
            self._next_request_id += 1
            request_line = (
                json.dumps({"id": request_id, "method": method_name, "args": list(arguments)})
                + "\n"
            ).encode()
            try:
                process.write_request(request_line, HELPER_WRITE_TIMEOUT)
            except OSError as write_error:
                # The helper acts only on a complete line, and this one did
                # not get through (see write_request), so nothing was sent.
                # The helper is ended either way: it may hold part of the
                # line, which must never be completed.
                self._process = None
                process.end(kill_now=True)
                raise CommandNotHandedOver(
                    "The command could not be handed to the command helper process "
                    f"({write_error}); it was not sent"
                ) from None
            self._call_in_flight = True
        self.requests_handed_over += 1

        try:
            reply = process.read_reply(HELPER_REPLY_TIMEOUT)
            if reply.get("id") != request_id:
                raise CommandHelperLost("the helper answered another request")
        except Exception as lost_error:
            # Any failure here, expected or not, leaves the request's fate
            # unknown, and the helper is not used again.
            logger.error(
                f"Command helper (pid {process.process_id}) lost during {method_name}: "
                f"{lost_error}"
            )
            self.helper_was_lost = True
            with self._process_lock:
                self._call_in_flight = False
                self._process = None
                # Either it has ended already, or it has been silent for
                # longer than any send to LinuxCNC takes.
                process.end(kill_now=True)
            raise CommandHelperLost(COMMAND_MAY_HAVE_BEEN_SENT_MESSAGE) from None

        serial_after_call = _plain_int_or_none(reply.get("serial"))
        if serial_after_call != process.command_serial:
            # The helper's command.serial moved: LinuxCNC took a message.
            self._serial = serial_after_call
        process.command_serial = serial_after_call
        with self._process_lock:
            self._call_in_flight = False
            if self._closed:
                self._end_idle_process()

        if reply.get("ok") is True:
            return
        error_text = str(reply.get("error", ""))
        if reply.get("is_linuxcnc_error"):
            raise self._linuxcnc_error_type(error_text)
        # The same type the call raised in the helper, where this process
        # has one of that name that can be built from the text alone.
        error_type = getattr(builtins, str(reply.get("error_type", "")), None)
        if isinstance(error_type, type) and issubclass(error_type, Exception):
            try:
                error_to_raise = error_type(error_text)
            except Exception:
                error_to_raise = RuntimeError(error_text)
        else:
            error_to_raise = RuntimeError(error_text)
        raise error_to_raise

    def __getattr__(self, method_name: str):
        if method_name.startswith("_"):
            raise AttributeError(method_name)
        return lambda *arguments: self._forward(method_name, arguments)


def _plain_int_or_none(value) -> int | None:
    """The value if it is a real int (not a bool), else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
