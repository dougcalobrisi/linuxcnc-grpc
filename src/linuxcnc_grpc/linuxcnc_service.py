"""
LinuxCNCService gRPC implementation.

Provides real LinuxCNC machine control and status via gRPC.
Requires the linuxcnc Python module and a running LinuxCNC instance.
"""

import errno
import os
import stat as stat_module
import subprocess
import sys
import time
import logging
import threading
import secrets
from collections import deque
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

import grpc
import linuxcnc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc
from .linuxcnc_mapper import LinuxCNCMapper
from .machine_config import build_machine_config
from .error_broker import BufferedError, ErrorBroker, ERROR_CHANNEL_ON_DEMAND

logger = logging.getLogger(__name__)

# Reconnection constants
INITIAL_RECONNECT_INTERVAL = 1  # seconds
MAX_RECONNECT_INTERVAL = 30  # seconds
# Longest pause between checks while waiting for LinuxCNC to appear. Shorter
# than MAX_RECONNECT_INTERVAL: a check is cheap, and nobody wants to wait half
# a minute after starting LinuxCNC.
MAX_WAIT_FOR_LINUXCNC_INTERVAL = 5.0  # seconds
# The reachability check runs in a separate Python process (see
# _linuxcnc_is_reachable for why). This is that process's whole program.
REACHABILITY_PROBE_CODE = "import linuxcnc; linuxcnc.stat().poll()"
REACHABILITY_PROBE_TIMEOUT = 10.0  # seconds

LINUXCNC_UNAVAILABLE_MESSAGE = (
    "LinuxCNC is not running, or the server has not connected to it yet"
)

# Input validation limits
MAX_MDI_COMMAND_LENGTH = 10000  # characters
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
# Most file bytes one ReadFile response carries. Well under the 4 MB that gRPC
# clients accept by default, so a client needs no special channel options.
MAX_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB
# An upload is written under a private name of this form, next to its
# destination, and only then given the destination's name. A random token
# follows the prefix. ListFiles leaves these names out.
UPLOAD_TEMPORARY_PREFIX = ".linuxcnc-grpc-upload-"
UPLOAD_TEMPORARY_SUFFIX = ".tmp"

# WaitComplete polling
DEFAULT_WAIT_COMPLETE_TIMEOUT = 5.0  # seconds, used when the request gives none
WAIT_COMPLETE_POLL_INTERVAL = 0.05  # seconds slept between polls, lock released
# Longest single attempt at the lock, in seconds. Between attempts the wait
# checks that its client is still there.
LOCK_ACQUIRE_SLICE = 0.1

# LinuxCNC's serials are C ints and wrap at 32 bits.
SERIAL_HALF_RANGE = 2**31
SERIAL_FULL_RANGE = 2**32

# How many of the LinuxCNC serials this server issued are remembered, so a
# WaitComplete naming one can be checked against the current connection.
ISSUED_SERIALS_REMEMBERED = 4096

# CommandResponse.serial is an int32; the server's own counter wraps to 1
# after this value.
MAX_RESPONSE_SERIAL = 2**31 - 1

# Error delivery
STATUS_ERRORS_LIMIT = 20  # most recent error messages included in LinuxCNCStatus
STREAM_ERRORS_WAIT_INTERVAL = 0.25  # seconds a StreamErrors call waits before rechecking the client

# Default allowed NC file directory (override with LINUXCNC_NC_FILES env var)
DEFAULT_NC_FILES_DIR = "/home/linuxcnc/linuxcnc/nc_files"


# SendCommand's error_message when LinuxCNC's command serial did not change
# across a dispatch, which means the write to its command channel failed.
COMMAND_NOT_ACCEPTED_MESSAGE = (
    "The command was not accepted by LinuxCNC: the write to its command "
    "channel failed, so nothing was sent"
)


class DispatchRecord(NamedTuple):
    """
    What WaitComplete needs to know about one dispatched command.

    Attributes:
        linuxcnc_serial: LinuxCNC's own serial for the command, read from
            ``linuxcnc.command().serial`` after dispatch. None when the
            bindings don't expose it.
        echo_serial_before_dispatch: ``stat.echo_serial_number`` sampled just
            before dispatch. Only recorded when ``linuxcnc_serial`` is None,
            as the fallback baseline.
    """

    linuxcnc_serial: int | None
    echo_serial_before_dispatch: int | None


def _signed_serial_difference(serial: int, reference_serial: int) -> int:
    """
    How far serial is ahead of reference_serial, as a signed 32-bit number.

    LinuxCNC subtracts serials into a C int, so the counter can wrap and
    the newer serial still compares as ahead. A plain Python comparison
    would get the order wrong on either side of the wrap.
    """
    return (
        (serial - reference_serial + SERIAL_HALF_RANGE) % SERIAL_FULL_RANGE
    ) - SERIAL_HALF_RANGE


def _as_plain_int(value) -> int | None:
    """Return value if it is a real int (not bool, not a stand-in object), else None."""
    # Serials get compared and subtracted, so anything that merely looks like
    # a number (a bool, or a mock's auto-created attribute) must not get through.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class LinuxCNCServiceServicer(linuxcnc_pb2_grpc.LinuxCNCServiceServicer):
    """
    gRPC servicer for LinuxCNC operations.

    By default the constructor needs a running LinuxCNC and raises if there
    is none. With require_linuxcnc=False it starts disconnected instead; the
    RPCs that need LinuxCNC then abort with UNAVAILABLE until the waiting
    thread (start_waiting_for_linuxcnc) has connected. Once connected, the
    servicer does not watch whether LinuxCNC stays up.
    """

    def __init__(
        self,
        nc_files_dir: str | None = None,
        error_channel_mode: str = ERROR_CHANNEL_ON_DEMAND,
        require_linuxcnc: bool = True,
        on_connection_change: Callable[[bool], None] | None = None,
    ):
        """
        Initialize the service with real LinuxCNC connection.

        Args:
            nc_files_dir: Override directory for NC files. If None, falls back
                to LINUXCNC_NC_FILES env var, then DEFAULT_NC_FILES_DIR.
            error_channel_mode: When the server reads LinuxCNC's error
                channel: "on-demand" (only while a StreamErrors client is
                connected), "always" or "off". See error_broker.
            require_linuxcnc: If True (the default), raise when LinuxCNC is
                not running. If False, start disconnected and leave
                connecting to start_waiting_for_linuxcnc().
            on_connection_change: Called with True when the servicer
                connects to LinuxCNC, from the thread that connected.

        Raises:
            Exception: If require_linuxcnc is True and LinuxCNC is not
                running (whatever the linuxcnc module raised).
        """
        self._lock = threading.RLock()
        self._command_serial = 0
        self._connected = False
        self._nc_files_dir = nc_files_dir
        self._on_connection_change = on_connection_change
        # Channels to LinuxCNC; None until the first successful _connect().
        self._stat = None
        self._command = None
        self._error_channel = None
        self._waiting_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()

        # Dispatch bookkeeping for WaitComplete (guarded by self._lock):
        # what LinuxCNC knows the most recent command this server dispatched
        # as. Reset whenever we reconnect.
        self._latest_dispatch_record: DispatchRecord | None = None
        # LinuxCNC serials handed out by SendCommand on the current
        # connection (guarded by self._lock, emptied by every _connect()).
        # WaitComplete only accepts a requested linuxcnc_serial found here.
        self._issued_linuxcnc_serials: deque[int] = deque(maxlen=ISSUED_SERIALS_REMEMBERED)
        # Incremented by every _connect(), so a WaitComplete in progress can
        # tell that the command channel it resolved its target on is gone.
        self._connection_generation = 0

        self._command_handlers = {
            "state": self._handle_state_cmd,
            "mode": self._handle_mode_cmd,
            "mdi": self._handle_mdi_cmd,
            "jog": self._handle_jog_cmd,
            "home": self._handle_home_cmd,
            "unhome": self._handle_unhome_cmd,
            "spindle": self._handle_spindle_cmd,
            "spindle_override": self._handle_spindle_override_cmd,
            "brake": self._handle_brake_cmd,
            "feedrate": self._handle_feedrate_cmd,
            "rapidrate": self._handle_rapidrate_cmd,
            "maxvel": self._handle_maxvel_cmd,
            "coolant": self._handle_coolant_cmd,
            "tool_offset": self._handle_tool_offset_cmd,
            "program": self._handle_program_cmd,
            "digital_output": self._handle_digital_output_cmd,
            "analog_output": self._handle_analog_output_cmd,
            "set_limit": self._handle_set_limit_cmd,
            "override_config": self._handle_override_config_cmd,
            "program_options": self._handle_program_options_cmd,
            "teleop": self._handle_teleop_cmd,
            "traj_mode": self._handle_traj_mode_cmd,
            "override_limits": self._handle_override_limits_cmd,
            "reset_interpreter": self._handle_reset_interpreter_cmd,
            "load_tool_table": self._handle_load_tool_table_cmd,
            "task_plan_sync": self._handle_task_plan_sync_cmd,
            "debug": self._handle_debug_cmd,
            "operator_message": self._handle_operator_message_cmd,
        }

        # Cache version from module attribute
        self._version = getattr(linuxcnc, 'version', 'unknown')

        # The single reader of LinuxCNC's error channel. Created before the
        # first connect and started after it, so it never polls a channel
        # that does not exist yet.
        self._error_broker = ErrorBroker(self._poll_error_channel, mode=error_channel_mode)

        # Establish initial connection
        if require_linuxcnc or self._linuxcnc_is_reachable():
            self._connect()
            logger.info(f"LinuxCNCService initialized (CONNECTED) - LinuxCNC v{self._version}")
        else:
            # Do not even try to connect from this process: an attempt made
            # while LinuxCNC is down leaves the linuxcnc module unable to
            # connect for the rest of the process's life.
            logger.warning(
                "LinuxCNC is not running yet; starting disconnected and waiting for it"
            )
        self._error_broker.start()

    def _connect(self) -> None:
        """Establish connection to LinuxCNC."""
        with self._lock:
            # Build and prove the new channels in locals before touching
            # anything shared. If any of this raises, the servicer keeps its
            # previous channels, its latest dispatch record and its
            # connection generation, so a WaitComplete in progress is not
            # left comparing an old serial against a half-replaced connection.
            new_stat = linuxcnc.stat()
            new_command = linuxcnc.command()
            new_error_channel = linuxcnc.error_channel()
            new_stat.poll()

            self._stat = new_stat
            self._command = new_command
            self._error_channel = new_error_channel
            # A new command channel starts its own serial bookkeeping, so the
            # record made through the previous one no longer means anything.
            self._latest_dispatch_record = None
            self._issued_linuxcnc_serials.clear()
            self._connection_generation += 1
            self._set_connected(True)

    def _set_connected(self, connected: bool) -> None:
        """
        Record the connection state and report a change to the listener.

        The listener is called with the lock held, so it runs before any
        RPC can use the new connection. It must therefore be quick, must not
        call back into the servicer from another thread, and must not call
        close() (the error reader could not be joined while this lock is
        held).
        """
        with self._lock:
            state_changed = connected != self._connected
            self._connected = connected
            if state_changed and self._on_connection_change is not None:
                try:
                    self._on_connection_change(connected)
                except Exception as e:
                    logger.error(f"Connection change listener failed: {e}")

    def _connection_snapshot(self) -> tuple[bool, int]:
        """The connection state and generation, read together under the lock."""
        with self._lock:
            return self._connected, self._connection_generation

    def _connect_if_unchanged_since(self, snapshot: tuple[bool, int]) -> bool:
        """
        Connect, unless the connection changed since ``snapshot`` was taken.

        A reachability check takes time and runs without the lock, so by the
        time it answers another thread may have connected already. Returns
        False without connecting in that case, or if the servicer is closing:
        replacing a connection another thread has just made would clear its
        dispatch tracking and make a WaitComplete in progress report a false
        RCS_DONE. The check and the connect happen in one lock hold. Raises
        what _connect() raises.
        """
        with self._lock:
            if self._stop_requested.is_set():
                return False
            if snapshot != (self._connected, self._connection_generation):
                return False
            self._connect()
            return True

    def _abort_if_disconnected(self, context: grpc.ServicerContext) -> bool:
        """
        Abort the RPC with UNAVAILABLE if there is no connection to LinuxCNC.

        Returns True if the RPC was aborted, so the caller can return. Once
        this has passed, the RPC can use the channels: a connection, once
        made, is only ever replaced by another one.
        """
        with self._lock:
            connected = self._connected
        if connected:
            return False
        context.abort(grpc.StatusCode.UNAVAILABLE, LINUXCNC_UNAVAILABLE_MESSAGE)
        return True

    @staticmethod
    def _linuxcnc_is_reachable() -> bool:
        """
        Whether LinuxCNC is running, checked from a separate Python process.

        This cannot be checked in this process (verified on the LinuxCNC 2.9
        simulator): a linuxcnc.stat() tried while LinuxCNC is down fails, and
        that one failed attempt breaks the linuxcnc module for the rest of
        the process. Every later attempt fails with "error return without
        exception set", even after LinuxCNC is up.

        A short-lived child process pays that price instead. It takes 7 ms
        with LinuxCNC running and 15 ms without, on the simulator. This
        process only ever calls _connect() right after a child succeeded.
        A check that cannot be made counts as "not running".
        """
        try:
            probe = subprocess.run(
                [sys.executable, "-c", REACHABILITY_PROBE_CODE],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=REACHABILITY_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"LinuxCNC reachability probe could not run: {e}")
            return False
        return probe.returncode == 0

    def start_waiting_for_linuxcnc(self) -> None:
        """
        Start the background thread that connects once LinuxCNC is running.

        It checks for LinuxCNC with exponential backoff
        (INITIAL_RECONNECT_INTERVAL doubling up to
        MAX_WAIT_FOR_LINUXCNC_INTERVAL, for as long as it takes), connects
        when it is there, and then ends. It does not watch the connection
        afterwards. Does nothing if LinuxCNC is already connected or the
        thread was already started.
        """
        with self._lock:
            if self._connected or self._waiting_thread is not None:
                return
            waiting_thread = threading.Thread(
                target=self._wait_for_linuxcnc,
                name="linuxcnc-connection-waiter",
                daemon=True,
            )
            # Keep the thread only once it runs: close() joins it, and a
            # thread that failed to start cannot be joined.
            waiting_thread.start()
            self._waiting_thread = waiting_thread

    def _wait_for_linuxcnc(self) -> None:
        """Waiting thread: check until LinuxCNC is running, connect, end."""
        retry_interval = INITIAL_RECONNECT_INTERVAL
        seconds_until_next_check = 0.0
        while not self._stop_requested.wait(seconds_until_next_check):
            seconds_until_next_check = retry_interval
            retry_interval = min(retry_interval * 2, MAX_WAIT_FOR_LINUXCNC_INTERVAL)
            snapshot = self._connection_snapshot()
            if snapshot[0]:
                return
            if not self._linuxcnc_is_reachable():
                continue
            try:
                # Does nothing if close() was called during the check.
                if self._connect_if_unchanged_since(snapshot):
                    logger.info(f"Connected to LinuxCNC v{self._version}")
            except Exception as e:
                # LinuxCNC went away between the check and the connect.
                # That failed attempt may have left the linuxcnc module
                # unusable in this process; keep trying, but say so.
                logger.error(
                    f"Connecting to LinuxCNC failed although it was reachable: {e}. "
                    "If this repeats while LinuxCNC is running, restart the server."
                )

    def _reconnect(self, failed_connection: tuple[bool, int] | None = None) -> bool:
        """
        Attempt to reconnect with exponential backoff.

        Called by an RPC whose poll of LinuxCNC raised.

        Args:
            failed_connection: _connection_snapshot() of the connection whose
                failure is the reason for reconnecting, taken in the lock
                hold in which it failed. If another thread has replaced the
                connection since, this leaves the new one alone: replacing it
                would drop the command a WaitComplete is waiting on. Without
                it, the connection as it is now is the one replaced.

        Returns:
            True if there is a new connection (made here or by another
            thread meanwhile), False if all attempts failed or the servicer
            is closing.
        """
        snapshot = (
            failed_connection if failed_connection is not None
            else self._connection_snapshot()
        )
        interval = INITIAL_RECONNECT_INTERVAL
        while interval <= MAX_RECONNECT_INTERVAL:
            if self._stop_requested.is_set():
                return False
            # Never try to connect from this process while LinuxCNC may be
            # down: one failed attempt breaks the linuxcnc module for good
            # (see _linuxcnc_is_reachable). Ask a child process first.
            if self._linuxcnc_is_reachable():
                try:
                    logger.info("Attempting to reconnect to LinuxCNC...")
                    if self._connect_if_unchanged_since(snapshot):
                        logger.info("Reconnected to LinuxCNC successfully")
                        return True
                    # Another RPC replaced the connection first (leave what
                    # it did alone), or the servicer is closing.
                    return not self._stop_requested.is_set()
                except Exception as e:
                    logger.warning(f"Reconnect failed, retrying in {interval}s: {e}")
            else:
                logger.warning(f"LinuxCNC is not reachable, retrying in {interval}s")
            time.sleep(interval)
            interval *= 2
        logger.error("Failed to reconnect to LinuxCNC after max retries")
        return False

    def _poll_error_channel(self):
        """Read one message from LinuxCNC's error channel, for the error broker."""
        with self._lock:
            if not self._connected:
                return None
            return self._error_channel.poll()

    def _error_to_proto(self, buffered_error: BufferedError) -> linuxcnc_pb2.ErrorMessage:
        """Convert a message held by the error broker to its protobuf form."""
        return linuxcnc_pb2.ErrorMessage(
            type=self._map_error_type(buffered_error.error_kind),
            message=buffered_error.message,
            timestamp=buffered_error.timestamp_ns,
            sequence=buffered_error.sequence,
        )

    def _recent_errors(self) -> list[linuxcnc_pb2.ErrorMessage]:
        """The newest error messages the server has read, for LinuxCNCStatus.errors."""
        return [
            self._error_to_proto(buffered_error)
            for buffered_error in self._error_broker.recent(STATUS_ERRORS_LIMIT)
        ]

    def _poll_and_map_status(self) -> linuxcnc_pb2.LinuxCNCStatus:
        """
        Poll LinuxCNC and build the status message. Caller must hold self._lock.
        """
        self._stat.poll()
        mapper = LinuxCNCMapper(self._stat, version=self._version)
        return mapper.map_to_proto(errors=self._recent_errors())

    def close(self) -> None:
        """Stop the servicer's background threads. Call when the server shuts down."""
        # Set under the lock: a connect checks the flag and publishes its
        # channels in one lock hold, so none can land after this returns.
        with self._lock:
            self._stop_requested.set()
            waiting_thread = self._waiting_thread
        self._error_broker.stop()
        if waiting_thread is not None and waiting_thread is not threading.current_thread():
            # Normally immediate. A reachability check in progress can hold
            # the thread a little longer; it exits right after without
            # connecting, so there is no need to wait that out.
            waiting_thread.join(timeout=2.0)

    def _next_serial(self) -> int:
        """Get next command serial number, within 1..MAX_RESPONSE_SERIAL."""
        with self._lock:
            self._command_serial = self._command_serial % MAX_RESPONSE_SERIAL + 1
            return self._command_serial

    def _acquire_lock_until(self, deadline: float, context: grpc.ServicerContext) -> bool:
        """
        Take self._lock, giving up at deadline (a time.monotonic() value)
        or when the RPC behind context is no longer active.

        Returns whether the lock was taken; the caller releases it. At or
        past the deadline the lock is still taken if it is free.
        """
        while True:
            seconds_remaining = max(0.0, deadline - time.monotonic())
            # Short attempts, so that a cancelled RPC gives up its worker
            # thread while another RPC still holds the lock. They also keep
            # the timeout far below threading.TIMEOUT_MAX, above which
            # acquire() raises OverflowError (an infinite deadline).
            if self._lock.acquire(timeout=min(seconds_remaining, LOCK_ACQUIRE_SLICE)):
                return True
            if seconds_remaining <= LOCK_ACQUIRE_SLICE:
                return False
            if not context.is_active():
                return False

    def _read_linuxcnc_command_serial(self) -> int | None:
        """
        Read LinuxCNC's serial for the command most recently sent.

        ``linuxcnc.command`` exposes it as the read-only ``serial`` member
        (LinuxCNC 2.8+). Returns None if the attribute is missing or isn't an
        int. Caller must hold self._lock.
        """
        return _as_plain_int(getattr(self._command, "serial", None))

    def _sample_echo_serial_for_fallback(self) -> int | None:
        """
        Sample the echo serial before a dispatch, for the WaitComplete fallback.

        Only needed when the bindings don't expose ``command.serial``; returns
        None otherwise so the normal path costs no extra poll. It has to be
        taken *before* the handler runs: LinuxCNC's send blocks until the echo
        serial has caught up, so a sample taken afterwards would never be seen
        to advance. Caller must hold self._lock.

        A failed poll is not swallowed: without the sample the command could
        not be tracked, and WaitComplete would report on an older command or
        on none. The exception reaches SendCommand, which answers RCS_ERROR
        without dispatching.
        """
        if self._read_linuxcnc_command_serial() is not None:
            return None
        self._stat.poll()
        return _as_plain_int(self._stat.echo_serial_number)

    def _record_dispatch(self, echo_serial_before_dispatch: int | None) -> int | None:
        """
        Remember the command just dispatched as the one WaitComplete waits on.

        Call only after the handler succeeded, in the same lock hold, so the
        serial read here belongs to this command. Caller must hold self._lock.

        Returns LinuxCNC's serial for the command, or None if the bindings
        don't expose it.
        """
        linuxcnc_serial = self._read_linuxcnc_command_serial()
        if linuxcnc_serial is None and echo_serial_before_dispatch is None:
            return None
        self._latest_dispatch_record = DispatchRecord(
            linuxcnc_serial=linuxcnc_serial,
            echo_serial_before_dispatch=(
                None if linuxcnc_serial is not None else echo_serial_before_dispatch
            ),
        )
        if linuxcnc_serial is not None:
            self._issued_linuxcnc_serials.append(linuxcnc_serial)
        return linuxcnc_serial

    @staticmethod
    def _completion_state(
        dispatch_record: DispatchRecord | None,
        echo_serial_number: int,
        task_rcs_state: int,
    ) -> int | None:
        """
        Decide whether the awaited command has finished.

        Returns linuxcnc.RCS_DONE or linuxcnc.RCS_ERROR once it has, or None
        while the wait should go on.
        """
        if dispatch_record is None:
            # Nothing this server sent is being tracked (nothing sent yet, or
            # a reconnect cleared the records), so there is no command to
            # wait for: report done straight away, whatever the task is
            # doing. This is the server's own rule; linuxcnc's
            # wait_complete() has no such case.
            return linuxcnc.RCS_DONE

        task_is_idle = task_rcs_state in (linuxcnc.RCS_DONE, linuxcnc.RCS_ERROR)

        if dispatch_record.linuxcnc_serial is not None:
            serials_ahead = _signed_serial_difference(
                echo_serial_number, dispatch_record.linuxcnc_serial
            )
            if serials_ahead > 0:
                # A later command has been echoed. The task state now
                # belongs to that later command, so nothing more can be
                # learned about ours here; ours may even still be running
                # (a move, with an override sent after it). Same rule as
                # linuxcnc's own wait_complete().
                return linuxcnc.RCS_DONE
            if serials_ahead == 0 and task_is_idle:
                return task_rcs_state
            return None  # not picked up yet, or still executing ours

        # Fallback: no LinuxCNC serial, so wait for the echo serial to move
        # past where it was before we dispatched, and for the task to be idle.
        echo_has_advanced = _signed_serial_difference(
            echo_serial_number, dispatch_record.echo_serial_before_dispatch
        ) > 0
        if echo_has_advanced and task_is_idle:
            return task_rcs_state
        return None

    def _validate_joint_index(self, index: int, is_joint: bool = False) -> None:
        """Validate joint or axis index is within bounds."""
        with self._lock:
            self._stat.poll()
            if is_joint:
                max_index = len(self._stat.joint)
            else:
                max_index = len(self._stat.axis)
        if index < -1 or index >= max_index:
            label = "joint" if is_joint else "axis"
            raise ValueError(f"{label} index {index} out of range (0..{max_index - 1})")
        if index == -1 and not is_joint:
            raise ValueError("axis index -1 is not valid")

    def _validate_spindle_index(self, index: int) -> None:
        """Validate spindle index is within bounds."""
        with self._lock:
            self._stat.poll()
            max_index = len(self._stat.spindle)
        if index < 0 or index >= max_index:
            raise ValueError(f"spindle index {index} out of range (0..{max_index - 1})")

    def _resolved_nc_files_dir(self) -> Path:
        """
        The NC files directory with symlinks resolved.

        Raises OSError (ELOOP) on every Python version if the directory path
        goes through more symbolic links than the kernel follows (a loop, or
        a very long chain).
        """
        configured_dir = Path(
            self._nc_files_dir
            or os.getenv("LINUXCNC_NC_FILES", DEFAULT_NC_FILES_DIR)
        )
        try:
            resolved_dir = configured_dir.resolve()
        except RuntimeError:
            # Older Pythons report a symlink loop from resolve() this way.
            raise OSError(
                errno.ELOOP, "Too many levels of symbolic links", str(configured_dir)
            ) from None
        # Newer Pythons resolve a symlink loop without complaint and return a
        # path that still contains the link, so ask the kernel. Any other
        # failure (a directory that does not exist yet) is left to the caller.
        try:
            os.stat(configured_dir)
        except OSError as stat_error:
            if stat_error.errno == errno.ELOOP:
                raise
        return resolved_dir

    def _validate_nc_path(self, filename: str) -> Path:
        """Validate and resolve a filename relative to the NC files directory.

        Returns the resolved absolute Path.
        Raises ValueError on any validation failure.
        """
        if not filename or not filename.strip():
            raise ValueError("Filename must not be empty")
        if "\x00" in filename:
            raise ValueError("Filename must not contain null bytes")
        # Worded as the kernel words it: the cause may be a loop or just a
        # chain of links longer than the kernel follows.
        too_many_links_message = f"Too many levels of symbolic links: {filename}"
        try:
            allowed_dir = Path(
                self._nc_files_dir
                or os.getenv("LINUXCNC_NC_FILES", DEFAULT_NC_FILES_DIR)
            ).resolve()
            # Newer Pythons resolve a symlink loop without complaint and
            # return a path that still contains the link, so ask the kernel.
            try:
                os.stat(allowed_dir / filename)
            except OSError as stat_error:
                if stat_error.errno == errno.ELOOP:
                    raise ValueError(too_many_links_message) from None
            resolved = (allowed_dir / filename).resolve()
        except RuntimeError:
            # Older Pythons report a symlink loop from resolve() this way.
            raise ValueError(too_many_links_message) from None
        if not resolved.is_relative_to(allowed_dir):
            raise ValueError(
                f"Path must be within allowed directory: {allowed_dir}"
            )
        return resolved

    @staticmethod
    def _stat_if_exists(path: Path) -> os.stat_result | None:
        """
        One os.stat of path (following links), or None if nothing is there.

        Callers take existence and file type from this one result, so the
        two cannot disagree when the path changes meanwhile. A path that
        runs into a symbolic link loop names nothing either.
        """
        try:
            return os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as stat_error:
            if stat_error.errno == errno.ELOOP:
                return None
            raise

    @staticmethod
    def _is_upload_temporary_name(name: str) -> bool:
        """Whether name is one UploadFile gives the file it is still writing."""
        return name.startswith(UPLOAD_TEMPORARY_PREFIX) and name.endswith(
            UPLOAD_TEMPORARY_SUFFIX
        )

    @staticmethod
    def _remove_upload_temporary_file(temporary_path: Path) -> None:
        """Remove an upload's temporary file; a failure is logged, not raised."""
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        except OSError as unlink_error:
            logger.error(
                f"UploadFile: could not remove temporary file {temporary_path}: {unlink_error}"
            )

    def _write_upload_temporary_file(
        self, directory: Path, content: str, permission_bits: int | None
    ) -> Path:
        """
        Write content to a new temporary file in directory and return its path.

        The name holds a random token, so it cannot collide with a user's
        file or another upload, and the file is created exclusively without
        following a link. It is created with the mode a plain open() gives
        (0666 less the umask); permission_bits, when given, replaces that.
        If anything fails the temporary file is removed.
        """
        temporary_path = directory / (
            f"{UPLOAD_TEMPORARY_PREFIX}{secrets.token_hex(16)}{UPLOAD_TEMPORARY_SUFFIX}"
        )
        file_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o666,
        )
        try:
            try:
                # Text mode with the default encoding, as write_text() used.
                temporary_file = os.fdopen(file_descriptor, "w")
            except BaseException:
                os.close(file_descriptor)
                raise
            with temporary_file:
                temporary_file.write(content)
                if permission_bits is not None:
                    os.fchmod(file_descriptor, permission_bits)
        except BaseException:
            self._remove_upload_temporary_file(temporary_path)
            raise
        return temporary_path

    # =========================================================================
    # GetStatus RPC
    # =========================================================================

    def GetStatus(
        self,
        request: linuxcnc_pb2.GetStatusRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.LinuxCNCStatus:
        """
        Get current LinuxCNC status.

        Polls the LinuxCNC stat channel and returns the current machine status.
        Attempts to reconnect if the connection is lost.
        """
        logger.debug("GetStatus called")
        if self._abort_if_disconnected(context):
            return
        connection_polled = None
        try:
            with self._lock:
                connection_polled = self._connection_snapshot()
                return self._poll_and_map_status()
        except linuxcnc.error as e:
            logger.error(f"LinuxCNC error in GetStatus: {e}")
            if self._reconnect(connection_polled):
                try:
                    with self._lock:
                        return self._poll_and_map_status()
                except Exception as retry_error:
                    logger.error(f"GetStatus failed after reconnect: {retry_error}")
            context.abort(grpc.StatusCode.UNAVAILABLE, f"LinuxCNC unavailable: {e}")

    # =========================================================================
    # SendCommand RPC
    # =========================================================================

    def SendCommand(
        self,
        request: linuxcnc_pb2.LinuxCNCCommand,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.CommandResponse:
        """
        Send a command to LinuxCNC.

        Dispatches to the appropriate command handler based on the command type.

        RCS_DONE in the response means the command was *dispatched* to
        LinuxCNC, not that it finished executing. The response serial is
        request.serial if the client set one, otherwise this server's own
        counter; it is not LinuxCNC's serial and must not be compared with
        echo_serial_number.

        LinuxCNC's real serial for the command is returned separately in
        linuxcnc_serial (unset if nothing was dispatched or the bindings
        don't expose it). It identifies the command handed to LinuxCNC; it
        does not prove the task received it. LinuxCNC's send normally returns
        once the task has echoed the serial, but it gives up after about 5
        seconds without an error if the task does not answer. WaitComplete
        (which can be given that value to wait on this command's serial) or
        the status confirms receipt and completion.

        A command LinuxCNC did not take is refused. When the write to
        LinuxCNC's command channel fails, the bindings return normally and
        leave command.serial at the previous command's value. So the serial
        is read before and after the handler, and if it did not change the
        answer is RCS_ERROR with no linuxcnc_serial, and nothing is recorded
        as dispatched. This needs bindings that expose command.serial.

        The comparison sees only "no message was taken at all". A handler
        that sends several messages (coolant, set_limit, override_config,
        program_options, and program open, which LinuxCNC sends as a close
        followed by the open) is answered RCS_DONE if at least one message
        was taken, and linuxcnc_serial is then the serial of the last
        message LinuxCNC took, which need not be the last one sent.
        """
        if self._abort_if_disconnected(context):
            return
        serial = request.serial or self._next_serial()
        command_type = request.WhichOneof("command")
        logger.debug(f"SendCommand: {command_type} (serial={serial})")

        try:
            handler = self._command_handlers.get(command_type)
            if handler:
                with self._lock:
                    echo_serial_before_dispatch = self._sample_echo_serial_for_fallback()
                    linuxcnc_serial_before_dispatch = self._read_linuxcnc_command_serial()
                    try:
                        handler(request)
                    except Exception:
                        # Some handlers send several LinuxCNC messages
                        # (coolant, set_limit, override_config, ...). If one
                        # fails after an earlier one went out, LinuxCNC has
                        # still been given something new to do, and that is
                        # what WaitComplete must wait on from now on.
                        linuxcnc_serial_after_failure = self._read_linuxcnc_command_serial()
                        if (
                            linuxcnc_serial_after_failure is not None
                            and linuxcnc_serial_after_failure != linuxcnc_serial_before_dispatch
                        ):
                            self._record_dispatch(None)
                        raise
                    linuxcnc_serial_after_dispatch = self._read_linuxcnc_command_serial()
                    if (
                        linuxcnc_serial_after_dispatch is not None
                        and linuxcnc_serial_after_dispatch == linuxcnc_serial_before_dispatch
                    ):
                        # LinuxCNC's send returns without raising when its
                        # write fails, and command.serial is then still the
                        # previous command's. Publishing or recording it
                        # would make a wait report on that other command.
                        logger.error(
                            f"Command not accepted by LinuxCNC: {command_type} "
                            f"(command serial stayed at {linuxcnc_serial_after_dispatch})"
                        )
                        return linuxcnc_pb2.CommandResponse(
                            serial=serial,
                            status=linuxcnc_pb2.RCS_ERROR,
                            error_message=COMMAND_NOT_ACCEPTED_MESSAGE,
                        )
                    linuxcnc_serial = self._record_dispatch(echo_serial_before_dispatch)
                response = linuxcnc_pb2.CommandResponse(
                    serial=serial,
                    status=linuxcnc_pb2.RCS_DONE,
                    error_message=""
                )
                if linuxcnc_serial is not None:
                    response.linuxcnc_serial = linuxcnc_serial
                return response
            else:
                logger.error(f"No handler for command type: {command_type}")
                return linuxcnc_pb2.CommandResponse(
                    serial=serial,
                    status=linuxcnc_pb2.RCS_ERROR,
                    error_message=f"Unknown command type: {command_type}"
                )
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return linuxcnc_pb2.CommandResponse(
                serial=serial,
                status=linuxcnc_pb2.RCS_ERROR,
                error_message=str(e)
            )

    def _handle_state_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle state command (estop, on, off)."""
        state = request.state.state
        state_map = {
            linuxcnc_pb2.STATE_ESTOP: linuxcnc.STATE_ESTOP,
            linuxcnc_pb2.STATE_ESTOP_RESET: linuxcnc.STATE_ESTOP_RESET,
            linuxcnc_pb2.STATE_ON: linuxcnc.STATE_ON,
            linuxcnc_pb2.STATE_OFF: linuxcnc.STATE_OFF,
        }
        if state in state_map:
            self._command.state(state_map[state])
            logger.info(f"State command: {state}")
        else:
            raise ValueError(f"Unknown state: {state}")

    def _handle_mode_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle mode command (manual, auto, mdi)."""
        mode = request.mode.mode
        mode_map = {
            linuxcnc_pb2.MODE_MANUAL: linuxcnc.MODE_MANUAL,
            linuxcnc_pb2.MODE_AUTO: linuxcnc.MODE_AUTO,
            linuxcnc_pb2.MODE_MDI: linuxcnc.MODE_MDI,
        }
        if mode in mode_map:
            self._command.mode(mode_map[mode])
            logger.info(f"Mode command: {mode}")
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def _handle_mdi_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle MDI command (execute G-code string)."""
        command = request.mdi.command
        logger.debug(f"MDI command received: {command!r}")
        if not command or not command.strip():
            raise ValueError("MDI command must not be empty")
        if "\x00" in command:
            raise ValueError("MDI command must not contain null bytes")
        if len(command) > MAX_MDI_COMMAND_LENGTH:
            raise ValueError(
                f"MDI command too long ({len(command)} chars, max {MAX_MDI_COMMAND_LENGTH})"
            )
        self._command.mdi(command)
        logger.info(f"MDI command: {command}")

    def _handle_jog_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle jog command (stop, continuous, increment)."""
        jog = request.jog
        self._validate_joint_index(jog.index, jog.is_joint)
        if jog.type == linuxcnc_pb2.JOG_STOP:
            self._command.jog(linuxcnc.JOG_STOP, jog.is_joint, jog.index)
        elif jog.type == linuxcnc_pb2.JOG_CONTINUOUS:
            self._command.jog(linuxcnc.JOG_CONTINUOUS, jog.is_joint, jog.index, jog.velocity)
        elif jog.type == linuxcnc_pb2.JOG_INCREMENT:
            self._command.jog(linuxcnc.JOG_INCREMENT, jog.is_joint, jog.index, jog.velocity, jog.increment)
        else:
            raise ValueError(f"Unknown jog type: {jog.type}")
        logger.info(f"Jog command: type={jog.type}, is_joint={jog.is_joint}, index={jog.index}")

    def _handle_home_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle home command (-1 for all joints)."""
        joint = request.home.joint
        if joint != -1:
            self._validate_joint_index(joint, is_joint=True)
        self._command.home(joint)
        logger.info(f"Home command: joint={joint}")

    def _handle_unhome_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle unhome command."""
        joint = request.unhome.joint
        if joint != -1:
            self._validate_joint_index(joint, is_joint=True)
        self._command.unhome(joint)
        logger.info(f"Unhome command: joint={joint}")

    def _handle_spindle_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle spindle command (forward, reverse, off, increase, decrease)."""
        sp = request.spindle
        self._validate_spindle_index(sp.spindle)
        if sp.command == linuxcnc_pb2.SPINDLE_CMD_FORWARD:
            self._command.spindle(linuxcnc.SPINDLE_FORWARD, sp.speed, sp.spindle, sp.wait_for_at_speed)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_REVERSE:
            self._command.spindle(linuxcnc.SPINDLE_REVERSE, sp.speed, sp.spindle, sp.wait_for_at_speed)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_OFF:
            self._command.spindle(linuxcnc.SPINDLE_OFF, 0, sp.spindle)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_INCREASE:
            self._command.spindle(linuxcnc.SPINDLE_INCREASE, 0, sp.spindle)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_DECREASE:
            self._command.spindle(linuxcnc.SPINDLE_DECREASE, 0, sp.spindle)
        else:
            raise ValueError(f"Unknown spindle command: {sp.command}")
        logger.info(f"Spindle command: cmd={sp.command}, speed={sp.speed}, spindle={sp.spindle}")

    def _handle_spindle_override_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle spindle override command."""
        so = request.spindle_override
        self._validate_spindle_index(so.spindle)
        self._command.spindleoverride(so.scale, so.spindle)
        logger.info(f"Spindle override: scale={so.scale}, spindle={so.spindle}")

    def _handle_brake_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle brake command (engage/release)."""
        brake = request.brake
        self._validate_spindle_index(brake.spindle)
        if brake.state == linuxcnc_pb2.BRAKE_ENGAGE:
            self._command.brake(linuxcnc.BRAKE_ENGAGE, brake.spindle)
        else:
            self._command.brake(linuxcnc.BRAKE_RELEASE, brake.spindle)
        logger.info(f"Brake command: state={brake.state}, spindle={brake.spindle}")

    def _handle_feedrate_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle feedrate override command."""
        scale = request.feedrate.scale
        self._command.feedrate(scale)
        logger.info(f"Feedrate command: scale={scale}")

    def _handle_rapidrate_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle rapid rate override command."""
        scale = request.rapidrate.scale
        self._command.rapidrate(scale)
        logger.info(f"Rapidrate command: scale={scale}")

    def _handle_maxvel_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle max velocity command."""
        velocity = request.maxvel.velocity
        self._command.maxvel(velocity)
        logger.info(f"Maxvel command: velocity={velocity}")

    def _handle_coolant_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle coolant command (mist and flood)."""
        c = request.coolant
        if c.mist:
            self._command.mist(linuxcnc.MIST_ON)
        else:
            self._command.mist(linuxcnc.MIST_OFF)
        if c.flood:
            self._command.flood(linuxcnc.FLOOD_ON)
        else:
            self._command.flood(linuxcnc.FLOOD_OFF)
        logger.info(f"Coolant command: mist={c.mist}, flood={c.flood}")

    def _handle_tool_offset_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle tool offset command."""
        to = request.tool_offset
        self._command.tool_offset(
            to.tool_number, to.z_offset, to.x_offset,
            to.diameter, to.front_angle, to.back_angle, to.orientation
        )
        logger.info(f"Tool offset command: tool={to.tool_number}")

    def _handle_program_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle program command (open, run, pause, resume, step, abort)."""
        p = request.program
        cmd_type = p.WhichOneof("command")
        if cmd_type == "open":
            resolved = self._validate_nc_path(p.open)
            self._command.program_open(str(resolved))
        elif cmd_type == "run_from_line":
            self._command.auto(linuxcnc.AUTO_RUN, p.run_from_line)
        elif cmd_type == "pause" and p.pause:
            self._command.auto(linuxcnc.AUTO_PAUSE)
        elif cmd_type == "resume" and p.resume:
            self._command.auto(linuxcnc.AUTO_RESUME)
        elif cmd_type == "step" and p.step:
            self._command.auto(linuxcnc.AUTO_STEP)
        elif cmd_type == "abort" and p.abort:
            self._command.abort()
        else:
            raise ValueError(f"Unknown program command: {cmd_type}")
        logger.info(f"Program command: type={cmd_type}")

    def _handle_digital_output_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle digital output command."""
        do = request.digital_output
        self._command.set_digital_output(do.index, do.value)
        logger.info(f"Digital output command: index={do.index}, value={do.value}")

    def _handle_analog_output_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle analog output command."""
        ao = request.analog_output
        self._command.set_analog_output(ao.index, ao.value)
        logger.info(f"Analog output command: index={ao.index}, value={ao.value}")

    def _handle_set_limit_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle set limit command."""
        sl = request.set_limit
        self._command.set_min_limit(sl.joint, sl.min_limit)
        self._command.set_max_limit(sl.joint, sl.max_limit)
        logger.info(f"Set limit command: joint={sl.joint}, min={sl.min_limit}, max={sl.max_limit}")

    def _handle_override_config_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle override configuration command."""
        oc = request.override_config
        self._command.set_feed_override(oc.feed_override_enable)
        self._command.set_spindle_override(oc.spindle_override_enable, oc.spindle)
        self._command.set_feed_hold(oc.feed_hold_enable)
        self._command.set_adaptive_feed(oc.adaptive_feed_enable)
        logger.info(f"Override config command: feed_enable={oc.feed_override_enable}, spindle_enable={oc.spindle_override_enable}")

    def _handle_program_options_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle program options command (optional stop, block delete)."""
        po = request.program_options
        self._command.set_optional_stop(po.optional_stop)
        self._command.set_block_delete(po.block_delete)
        logger.info(f"Program options command: optional_stop={po.optional_stop}, block_delete={po.block_delete}")

    def _handle_teleop_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle teleop command."""
        enable = request.teleop.enable
        self._command.teleop_enable(enable)
        logger.info(f"Teleop command: enable={enable}")

    def _handle_traj_mode_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle trajectory mode command (free, coord, teleop)."""
        mode = request.traj_mode.mode
        mode_map = {
            linuxcnc_pb2.TRAJ_MODE_FREE: linuxcnc.TRAJ_MODE_FREE,
            linuxcnc_pb2.TRAJ_MODE_COORD: linuxcnc.TRAJ_MODE_COORD,
            linuxcnc_pb2.TRAJ_MODE_TELEOP: linuxcnc.TRAJ_MODE_TELEOP,
        }
        if mode in mode_map:
            self._command.traj_mode(mode_map[mode])
            logger.info(f"Traj mode command: mode={mode}")
        else:
            raise ValueError(f"Unknown traj mode: {mode}")

    def _handle_override_limits_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle override limits command (for recovery from limit switches)."""
        self._command.override_limits()
        logger.info("Override limits command")

    def _handle_reset_interpreter_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle reset interpreter command."""
        self._command.reset_interpreter()
        logger.info("Reset interpreter command")

    def _handle_load_tool_table_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle load tool table command (reloads from INI file)."""
        self._command.load_tool_table()
        logger.info("Load tool table command")

    def _handle_task_plan_sync_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle task plan sync command."""
        self._command.task_plan_synch()
        logger.info("Task plan sync command")

    def _handle_debug_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle debug command."""
        level = request.debug.level
        self._command.debug(level)
        logger.info(f"Debug command: level={level}")

    def _handle_operator_message_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle operator message command (error, text, display)."""
        om = request.operator_message
        if om.type == linuxcnc_pb2.OperatorMessageCommand.ERROR:
            self._command.error_msg(om.message)
        elif om.type == linuxcnc_pb2.OperatorMessageCommand.TEXT:
            self._command.text_msg(om.message)
        elif om.type == linuxcnc_pb2.OperatorMessageCommand.DISPLAY:
            self._command.display_msg(om.message)
        else:
            raise ValueError(f"Unknown operator message type: {om.type}")
        logger.info(f"Operator message command: type={om.type}, msg={om.message}")

    # =========================================================================
    # WaitComplete RPC
    # =========================================================================

    def WaitComplete(
        self,
        request: linuxcnc_pb2.WaitCompleteRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.CommandResponse:
        """
        Wait for command completion.

        Polls LinuxCNC status until the most recent command this server
        dispatched has finished, or the timeout expires. The lock is held
        only for each individual poll and released while sleeping, so other
        RPCs (an estop in particular) are never blocked behind a wait.

        request.serial is ignored, as it always has been: the wait is on the
        latest command dispatched when the call arrives, and the serial is
        only echoed back in the response. If there is no such command
        (nothing sent since connecting), it returns RCS_DONE at once. A
        command sent during the wait is not followed: once LinuxCNC has
        moved past the awaited one, the answer is RCS_DONE.

        If request.linuxcnc_serial is set (the value SendCommand returned),
        the wait applies the same serial rule to that serial instead. The
        serial must be one this server issued on its current connection to
        LinuxCNC; any other gets RCS_ERROR, and so does a reconnect during
        such a wait, because serials from before a restart of LinuxCNC mean
        nothing afterwards.

        Either way the rule is LinuxCNC's own (see _completion_state): once
        the echo serial is past the awaited one the answer is RCS_DONE,
        whatever became of the awaited command. The wait is exact only while
        nothing else is sent to LinuxCNC, by this server or any other client.
        A reconnect between the last poll and the response is not noticed.

        Returns RCS_DONE or RCS_ERROR ("Command failed") on completion, and
        RCS_EXEC with an empty error_message on timeout.

        request.timeout of 0, a negative number or NaN means the default. An
        infinite timeout waits until the command finishes, the client goes
        away or the server shuts down.
        """
        # NaN fails the comparison, so it gets the default like 0 does.
        timeout = request.timeout if request.timeout > 0 else DEFAULT_WAIT_COMPLETE_TIMEOUT
        deadline = time.monotonic() + timeout
        # Every lock acquisition below gives up at the deadline or when the
        # client has gone, so a wait cannot overrun its timeout, or outlive
        # its client, behind another RPC that holds the lock. Either way
        # the answer is the one for a timeout.
        timed_out_response = linuxcnc_pb2.CommandResponse(
            serial=request.serial,
            status=linuxcnc_pb2.RCS_EXEC,
            error_message="",
        )

        # Same check as _abort_if_disconnected, with the deadline.
        if not self._acquire_lock_until(deadline, context):
            return timed_out_response
        try:
            connected = self._connected
        finally:
            self._lock.release()
        if not connected:
            context.abort(grpc.StatusCode.UNAVAILABLE, LINUXCNC_UNAVAILABLE_MESSAGE)
            return
        logger.debug(f"WaitComplete called - serial={request.serial}, timeout={timeout}")

        try:
            waits_on_requested_serial = request.HasField("linuxcnc_serial")
            if not self._acquire_lock_until(deadline, context):
                return timed_out_response
            try:
                if (
                    waits_on_requested_serial
                    and request.linuxcnc_serial not in self._issued_linuxcnc_serials
                ):
                    # Not a serial SendCommand returned since this server
                    # connected: made up, very old, or from before a
                    # reconnect. Comparing it with the echo serial could
                    # report on some other command.
                    return linuxcnc_pb2.CommandResponse(
                        serial=request.serial,
                        status=linuxcnc_pb2.RCS_ERROR,
                        error_message=(
                            f"linuxcnc_serial {request.linuxcnc_serial} was not issued by "
                            "this server on its current connection to LinuxCNC; the "
                            "outcome of that command is unknown"
                        ),
                    )
                if waits_on_requested_serial:
                    dispatch_record = DispatchRecord(
                        linuxcnc_serial=request.linuxcnc_serial,
                        echo_serial_before_dispatch=None,
                    )
                else:
                    dispatch_record = self._latest_dispatch_record
                resolved_connection_generation = self._connection_generation
            finally:
                self._lock.release()

            result = None
            while True:
                if not self._acquire_lock_until(deadline, context):
                    break
                try:
                    if self._connection_generation != resolved_connection_generation:
                        if waits_on_requested_serial:
                            # The caller named a serial from the old
                            # connection. LinuxCNC may have restarted and
                            # begun counting again, so comparing it with the
                            # new echo serial could report on the wrong
                            # command. Say so instead of guessing.
                            return linuxcnc_pb2.CommandResponse(
                                serial=request.serial,
                                status=linuxcnc_pb2.RCS_ERROR,
                                error_message=(
                                    "Reconnected to LinuxCNC during the wait; the outcome "
                                    f"of command {request.linuxcnc_serial} is unknown"
                                ),
                            )
                        # Reconnected while we were waiting: the record we
                        # took belongs to the old command channel. Wait on
                        # the latest command sent since, if any.
                        dispatch_record = self._latest_dispatch_record
                        resolved_connection_generation = self._connection_generation
                    self._stat.poll()
                    echo_serial_number = self._stat.echo_serial_number
                    task_rcs_state = self._stat.state
                finally:
                    self._lock.release()

                result = self._completion_state(
                    dispatch_record, echo_serial_number, task_rcs_state
                )
                if result is not None:
                    break

                seconds_remaining = deadline - time.monotonic()
                if (
                    seconds_remaining <= 0
                    or not context.is_active()
                    or self._stop_requested.is_set()
                ):
                    break
                time.sleep(min(WAIT_COMPLETE_POLL_INTERVAL, seconds_remaining))

            if result == linuxcnc.RCS_DONE:
                status = linuxcnc_pb2.RCS_DONE
                error_message = ""
            elif result == linuxcnc.RCS_ERROR:
                status = linuxcnc_pb2.RCS_ERROR
                error_message = "Command failed"
            else:  # timed out (or client went away) - still executing
                status = linuxcnc_pb2.RCS_EXEC
                error_message = ""

            return linuxcnc_pb2.CommandResponse(
                serial=request.serial,
                status=status,
                error_message=error_message
            )
        except linuxcnc.error as e:
            logger.error(f"LinuxCNC error in WaitComplete: {e}")
            return linuxcnc_pb2.CommandResponse(
                serial=request.serial,
                status=linuxcnc_pb2.RCS_ERROR,
                error_message=str(e)
            )

    # =========================================================================
    # StreamStatus RPC
    # =========================================================================

    def StreamStatus(
        self,
        request: linuxcnc_pb2.StreamStatusRequest,
        context: grpc.ServicerContext
    ) -> Iterator[linuxcnc_pb2.LinuxCNCStatus]:
        """
        Stream status updates.

        Polls LinuxCNC at the requested interval and streams status updates.
        Attempts to reconnect if the connection is lost.
        """
        interval_ms = request.interval_ms if request.interval_ms > 0 else 100
        interval_sec = interval_ms / 1000.0
        if self._abort_if_disconnected(context):
            return

        logger.info(f"StreamStatus started - interval={interval_ms}ms")

        try:
            while context.is_active():
                if self._abort_if_disconnected(context):
                    return
                connection_polled = None
                try:
                    with self._lock:
                        connection_polled = self._connection_snapshot()
                        status = self._poll_and_map_status()
                    yield status
                except linuxcnc.error as e:
                    logger.error(f"LinuxCNC error in StreamStatus: {e}")
                    if not self._reconnect(connection_polled):
                        context.abort(grpc.StatusCode.UNAVAILABLE, f"LinuxCNC unavailable: {e}")
                        return
                time.sleep(interval_sec)
        except grpc.RpcError:
            pass  # Client disconnected; normal shutdown
        except OSError as e:
            logger.error(f"StreamStatus error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, f"Stream failed: {e}")
        finally:
            logger.info("StreamStatus ended")

    # =========================================================================
    # UploadFile RPC
    # =========================================================================

    def UploadFile(
        self,
        request: linuxcnc_pb2.UploadFileRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.UploadFileResponse:
        """Upload a G-code file to the nc_files directory."""
        logger.debug(f"UploadFile called: filename={request.filename!r}")
        try:
            if not request.content:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "File content must not be empty"
                )
                return
            if len(request.content) > MAX_UPLOAD_SIZE:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"File content too large ({len(request.content)} characters, max {MAX_UPLOAD_SIZE})"
                )
                return

            resolved = self._validate_nc_path(request.filename)
            existing_file_stat = self._stat_if_exists(resolved)
            overwritten = existing_file_stat is not None

            if overwritten and request.fail_if_exists:
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    f"File already exists: {request.filename}"
                )
                return
            if resolved == self._resolved_nc_files_dir() or (
                overwritten and stat_module.S_ISDIR(existing_file_stat.st_mode)
            ):
                # Refused before anything is written: the temporary file
                # goes next to the destination, and next to the NC files
                # directory itself is outside it.
                raise IsADirectoryError(errno.EISDIR, "Is a directory", str(resolved))
            if overwritten and not os.access(resolved, os.W_OK):
                # A rename only needs a writable directory. Writing in
                # place needed a writable file, and a program made
                # read-only must stay refused.
                raise PermissionError(errno.EACCES, "Permission denied", str(resolved))

            resolved.parent.mkdir(parents=True, exist_ok=True)
            # The content never appears under the destination name until it
            # is complete, and a failure only ever removes the temporary
            # file, so it cannot remove a file another request put there.
            # A replaced file keeps its permission bits.
            temporary_path = self._write_upload_temporary_file(
                resolved.parent,
                request.content,
                stat_module.S_IMODE(existing_file_stat.st_mode) if overwritten else None,
            )
            try:
                if request.fail_if_exists:
                    # A hard link fails if the destination exists, whatever
                    # is there (a dangling link included): of two uploads of
                    # one new name only one gets it.
                    try:
                        os.link(temporary_path, resolved)
                    except FileExistsError:
                        context.abort(
                            grpc.StatusCode.ALREADY_EXISTS,
                            f"File already exists: {request.filename}"
                        )
                        return
                    except OSError as link_error:
                        # No hard links here (some network and FAT mounts).
                        # There is no other way to create a complete file
                        # only if the name is free, so nothing is written.
                        raise OSError(
                            link_error.errno,
                            "fail_if_exists needs a file system with hard links "
                            f"({link_error.strerror}); upload without fail_if_exists",
                            str(resolved),
                        ) from link_error
                else:
                    # A rename replaces the destination in one step and does
                    # not follow a link put there after validation.
                    os.replace(temporary_path, resolved)
            finally:
                # After a rename the temporary name is already gone.
                self._remove_upload_temporary_file(temporary_path)
            logger.info(f"UploadFile: wrote {len(request.content)} characters to {resolved}")

            return linuxcnc_pb2.UploadFileResponse(
                path=str(resolved),
                overwritten=overwritten
            )
        except ValueError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except OSError as e:
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to write file: {e}")

    # =========================================================================
    # ListFiles RPC
    # =========================================================================

    def ListFiles(
        self,
        request: linuxcnc_pb2.ListFilesRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.ListFilesResponse:
        """List files in the nc_files directory."""
        logger.debug(f"ListFiles called: subdirectory={request.subdirectory!r}")
        try:
            allowed_dir = self._resolved_nc_files_dir()

            if request.subdirectory:
                target_dir = self._validate_nc_path(request.subdirectory)
            else:
                target_dir = allowed_dir

            if not target_dir.is_dir():
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Directory not found: {request.subdirectory}"
                )
                return

            files = []
            for entry in sorted(target_dir.iterdir()):
                # An upload in progress, or one a crash left unfinished.
                if self._is_upload_temporary_name(entry.name):
                    continue
                stat = entry.stat()
                files.append(linuxcnc_pb2.FileInfo(
                    name=entry.name,
                    path=str(entry.relative_to(allowed_dir)),
                    size_bytes=stat.st_size,
                    modified_timestamp=int(stat.st_mtime * 1e9),
                    is_directory=entry.is_dir()
                ))

            return linuxcnc_pb2.ListFilesResponse(
                files=files,
                directory=str(target_dir)
            )
        except ValueError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except OSError as e:
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to list files: {e}")

    # =========================================================================
    # DeleteFile RPC
    # =========================================================================

    def DeleteFile(
        self,
        request: linuxcnc_pb2.DeleteFileRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.DeleteFileResponse:
        """Delete a file from the nc_files directory."""
        logger.debug(f"DeleteFile called: filename={request.filename!r}")
        try:
            resolved = self._validate_nc_path(request.filename)

            if not resolved.exists():
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"File not found: {request.filename}"
                )
                return
            if resolved.is_dir():
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "Cannot delete directories"
                )
                return

            resolved.unlink()
            logger.info(f"DeleteFile: removed {resolved}")

            return linuxcnc_pb2.DeleteFileResponse(path=str(resolved))
        except ValueError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except OSError as e:
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to delete file: {e}")

    # =========================================================================
    # ReadFile RPC
    # =========================================================================

    def ReadFile(
        self,
        request: linuxcnc_pb2.ReadFileRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.ReadFileResponse:
        """
        Read a file, or one chunk of it, from the nc_files directory.

        A response carries at most MAX_READ_CHUNK_BYTES. Larger files are read
        with repeated calls, advancing request.offset.
        """
        logger.debug(
            f"ReadFile called: filename={request.filename!r}, "
            f"offset={request.offset}, max_bytes={request.max_bytes}"
        )
        try:
            if request.offset < 0:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "offset must not be negative")
                return
            if request.max_bytes < 0:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "max_bytes must not be negative")
                return

            resolved = self._validate_nc_path(request.filename)

            if not resolved.exists():
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"File not found: {request.filename}"
                )
                return
            if not resolved.is_file():
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "Only regular files can be read"
                )
                return

            chunk_size = request.max_bytes or MAX_READ_CHUNK_BYTES
            chunk_size = min(chunk_size, MAX_READ_CHUNK_BYTES)

            # The resolved path contains no symlinks. O_NOFOLLOW makes the
            # open fail if the file itself was swapped for one after the
            # check above, so the read cannot be redirected outside the
            # directory that way. O_NONBLOCK keeps the open from hanging if
            # it was swapped for a FIFO; the regular-file check below then
            # rejects it.
            file_descriptor = os.open(
                resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            try:
                nc_file = os.fdopen(file_descriptor, "rb")
            except BaseException:
                # fdopen did not take the descriptor over, so close it here.
                os.close(file_descriptor)
                raise
            with nc_file:
                stat_before_read = os.fstat(nc_file.fileno())
                if not stat_module.S_ISREG(stat_before_read.st_mode):
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "Only regular files can be read"
                    )
                    return
                nc_file.seek(request.offset)
                content = nc_file.read(chunk_size)
                # Size and mtime are taken from the open file, after the
                # read: they describe the file the content came from, and a
                # file truncated just before the read reports its new size,
                # so end_of_file is right for the bytes actually returned.
                file_stat = os.fstat(nc_file.fileno())
                # A file rewritten during the read would pair bytes from
                # one version with the size and mtime of another.
                if (
                    file_stat.st_size != stat_before_read.st_size
                    or file_stat.st_mtime_ns != stat_before_read.st_mtime_ns
                ):
                    context.abort(
                        grpc.StatusCode.ABORTED,
                        f"File changed while it was being read: {request.filename}; "
                        "start reading it again from offset 0"
                    )
                    return

            return linuxcnc_pb2.ReadFileResponse(
                path=str(resolved),
                content=content,
                offset=request.offset,
                total_size_bytes=file_stat.st_size,
                end_of_file=request.offset + len(content) >= file_stat.st_size,
                modified_timestamp=file_stat.st_mtime_ns,
            )
        except ValueError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except OSError as e:
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to read file: {e}")

    # =========================================================================
    # ResolveFile RPC
    # =========================================================================

    @staticmethod
    def _name_follows_symlink(allowed_dir: Path, filename: str) -> bool:
        """
        Whether resolving filename under allowed_dir goes through a symlink.

        Walks the name one component at a time, the way the kernel resolves
        it. Comparing the resolved path with the lexically normalised one is
        not enough: "link/../part.ngc" follows the link and can still end up
        at the path the lexical form names.
        """
        current_path = allowed_dir
        for path_component in Path(filename).parts:
            candidate_path = current_path / path_component
            if candidate_path.is_symlink():
                return True
            current_path = Path(os.path.realpath(candidate_path))
        return False

    def ResolveFile(
        self,
        request: linuxcnc_pb2.ResolveFileRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.ResolveFileResponse:
        """
        Resolve a name in the nc_files directory and compare it with the
        program LinuxCNC has loaded.

        Lets a client decide whether replacing or deleting the name would
        touch the loaded program, without resolving symlinks remotely.
        """
        logger.debug(f"ResolveFile called: filename={request.filename!r}")
        try:
            # The directory first: if it cannot be resolved, that is the
            # server's problem (INTERNAL), not a bad file name.
            allowed_dir = self._resolved_nc_files_dir()
            resolved = self._validate_nc_path(request.filename)
            follows_symlink = self._name_follows_symlink(allowed_dir, request.filename)

            if self._abort_if_disconnected(context):
                return
            try:
                with self._lock:
                    self._stat.poll()
                    loaded_program_as_reported = self._stat.file
                    interpreter_state = LinuxCNCMapper(
                        self._stat, version=self._version
                    )._map_interp_state(self._stat.interp_state)
            except linuxcnc.error as e:
                logger.error(f"LinuxCNC error in ResolveFile: {e}")
                context.abort(grpc.StatusCode.UNAVAILABLE, f"LinuxCNC unavailable: {e}")
                return

            loaded_program = ""
            is_loaded_program = False
            loaded_program_path = None
            if loaded_program_as_reported:
                try:
                    loaded_program_path = Path(loaded_program_as_reported).resolve()
                except RuntimeError:
                    # Older Pythons report a symlink loop from resolve() this
                    # way. A path that cannot be resolved is not this file;
                    # it is returned as LinuxCNC reported it.
                    loaded_program = loaded_program_as_reported
            # One stat answers exists, is_directory and the identity check
            # below, so the three agree even if the file changes meanwhile.
            resolved_file_stat = self._stat_if_exists(resolved)
            if loaded_program_path is not None:
                loaded_program = str(loaded_program_path)
                is_loaded_program = loaded_program_path == resolved
                if not is_loaded_program and resolved_file_stat is not None:
                    # A hard link is the same file under another name. A
                    # loaded program that is gone by now is not this file.
                    loaded_program_stat = self._stat_if_exists(loaded_program_path)
                    is_loaded_program = (
                        loaded_program_stat is not None
                        and os.path.samestat(resolved_file_stat, loaded_program_stat)
                    )

            return linuxcnc_pb2.ResolveFileResponse(
                path=str(resolved),
                exists=resolved_file_stat is not None,
                is_directory=(
                    resolved_file_stat is not None
                    and stat_module.S_ISDIR(resolved_file_stat.st_mode)
                ),
                is_symlink=follows_symlink,
                is_loaded_program=is_loaded_program,
                loaded_program=loaded_program,
                interp_state=interpreter_state,
            )
        except ValueError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except OSError as e:
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to resolve file: {e}")

    # =========================================================================
    # GetMachineConfig RPC
    # =========================================================================

    def GetMachineConfig(
        self,
        request: linuxcnc_pb2.GetMachineConfigRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.MachineConfig:
        """
        Get the machine's configuration: kinematics, joint-to-axis mapping,
        limits, velocities and units.

        Limits, units, the axis mask and counts come from live status. The
        rest is read from the INI file LinuxCNC was started with, on every
        call. If that file can't be read, the status-based fields are still
        returned and ini_readable is False.
        """
        logger.debug("GetMachineConfig called")
        if self._abort_if_disconnected(context):
            return
        try:
            with self._lock:
                self._stat.poll()
                ini_filename = self._stat.ini_filename
                try:
                    ini_file = linuxcnc.ini(ini_filename)
                    ini_lookup = ini_file.find
                except Exception as e:
                    # linuxcnc.error when the file can't be opened. The server
                    # may run as a user that cannot read the config directory.
                    logger.warning(f"Could not read INI file {ini_filename!r}: {e}")
                    ini_lookup = None
                allowed_dir = self._resolved_nc_files_dir()
                return build_machine_config(
                    self._stat,
                    ini_lookup,
                    nc_files_directory=str(allowed_dir),
                    kinematics_type=LinuxCNCMapper(
                        self._stat, version=self._version
                    )._map_kinematics_type(self._stat.kinematics_type),
                )
        except linuxcnc.error as e:
            logger.error(f"LinuxCNC error in GetMachineConfig: {e}")
            context.abort(grpc.StatusCode.UNAVAILABLE, f"LinuxCNC unavailable: {e}")
        except OSError as e:
            context.abort(
                grpc.StatusCode.INTERNAL, f"Failed to resolve the NC files directory: {e}"
            )

    # =========================================================================
    # StreamErrors RPC
    # =========================================================================

    def _map_error_type(self, linuxcnc_type: int) -> int:
        """Map linuxcnc error type constant to proto ErrorType enum."""
        mapping = {
            linuxcnc.OPERATOR_ERROR: linuxcnc_pb2.ErrorMessage.OPERATOR_ERROR,
            linuxcnc.OPERATOR_TEXT: linuxcnc_pb2.ErrorMessage.OPERATOR_TEXT,
            linuxcnc.OPERATOR_DISPLAY: linuxcnc_pb2.ErrorMessage.OPERATOR_DISPLAY,
            linuxcnc.NML_ERROR: linuxcnc_pb2.ErrorMessage.NML_ERROR,
            linuxcnc.NML_TEXT: linuxcnc_pb2.ErrorMessage.NML_TEXT,
            linuxcnc.NML_DISPLAY: linuxcnc_pb2.ErrorMessage.NML_DISPLAY,
        }
        return mapping.get(linuxcnc_type, linuxcnc_pb2.ErrorMessage.ERROR_TYPE_UNSPECIFIED)

    def StreamErrors(
        self,
        request: linuxcnc_pb2.StreamErrorsRequest,
        context: grpc.ServicerContext
    ) -> Iterator[linuxcnc_pb2.ErrorMessage]:
        """
        Stream messages from LinuxCNC's error channel.

        The error broker is the only reader of the channel; this call just
        subscribes to it, so any number of clients can stream at once and
        each receives every message. With request.replay_buffered, the
        stream starts with the messages already in the broker's buffer.

        Reading the channel takes messages away from the machine's GUI. In
        "on-demand" mode (the default) the server reads only while at least
        one subscriber is connected; in "off" mode this call is refused.
        """
        if not self._error_broker.reads_error_channel:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "The server is configured not to read LinuxCNC's error channel "
                "(--error-channel off)"
            )
            return
        if self._abort_if_disconnected(context):
            return

        logger.info(f"StreamErrors started - replay_buffered={request.replay_buffered}")

        try:
            with self._error_broker.subscribe(
                replay_buffered=request.replay_buffered
            ) as subscription:
                # Send the response headers now, so a client can tell that
                # its subscription is in place before the first message.
                context.send_initial_metadata(())
                # Also ends when the broker is stopped (server shutdown):
                # wait_for_messages() then returns at once, and looping on
                # would spin until the client went away.
                while context.is_active() and not self._error_broker.is_stopped:
                    for buffered_error in subscription.wait_for_messages(
                        timeout=STREAM_ERRORS_WAIT_INTERVAL
                    ):
                        yield self._error_to_proto(buffered_error)
        except grpc.RpcError:
            pass  # Client disconnected; normal shutdown
        except OSError as e:
            logger.error(f"StreamErrors error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, f"Stream failed: {e}")
        finally:
            logger.info("StreamErrors ended")
