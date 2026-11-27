"""
HalService gRPC implementation.

Provides HAL introspection via gRPC. This is READ-ONLY introspection -
no component creation or modification is supported.
"""

import time
import logging
import fnmatch
import threading
from typing import Iterator, Dict, Any, List, Optional

import grpc

# Fail fast if HAL module is not available
try:
    import hal
except ImportError as e:
    raise RuntimeError(
        "HAL module not available. This service requires a running "
        "LinuxCNC/HAL environment."
    ) from e

from linuxcnc_pb import hal_pb2, hal_pb2_grpc
from .hal_mapper import (
    HalMapper,
    HalSystemInfo,
    read_hal_message_level,
    read_hal_system_info,
    read_hal_type_constants,
    values_differ,
)

logger = logging.getLogger(__name__)

# The serial fields in the responses are int32; the server's own counter
# wraps to 1 after this value.
MAX_RESPONSE_SERIAL = 2**31 - 1

# Streaming RPCs poll at the interval the client asks for, within these bounds.
DEFAULT_STREAM_INTERVAL_MS = 100
MAX_STREAM_INTERVAL_SECONDS = 60.0

# A stream ends with an error status after this many failed polls in a row.
MAX_CONSECUTIVE_POLL_ERRORS = 5

# Most names one WatchValues call may watch; every name is read on every poll.
MAX_WATCHED_NAMES = 1000

# StreamStatus asks HAL which pins have a writer at most this often. Each
# answer costs a walk of HAL's pin list, and links rarely change.
PIN_WRITERS_REFRESH_SECONDS = 2.0

# SendCommand cannot return query results; these commands have their own RPC.
DEDICATED_RPC_FOR_COMMAND = {
    'get_value': 'GetValue',
    'query_pins': 'QueryPins',
    'query_signals': 'QuerySignals',
    'query_params': 'QueryParams',
    'query_components': 'QueryComponents',
}


def stream_interval_seconds(interval_ms: int) -> float:
    """
    Turn a requested poll interval into seconds.

    Zero or less means the default (100 ms). Anything above
    MAX_STREAM_INTERVAL_SECONDS is clamped to it, not rejected.
    """
    if interval_ms <= 0:
        interval_ms = DEFAULT_STREAM_INTERVAL_MS
    return min(interval_ms / 1000.0, MAX_STREAM_INTERVAL_SECONDS)


def watch_for_cancellation(context: grpc.ServicerContext) -> threading.Event:
    """
    Return an event that is set when the RPC ends (cancel, disconnect, deadline).

    Waiting on it instead of sleeping lets a stream give its worker thread
    back at once. It is already set if the RPC ended before this call.
    """
    cancelled = threading.Event()
    if not context.add_callback(cancelled.set):
        cancelled.set()
    return cancelled


class HalServiceServicer(hal_pb2_grpc.HalServiceServicer):
    """
    gRPC servicer for HAL introspection operations.

    This service provides READ-ONLY access to HAL pins, signals, parameters,
    and components. Modification commands (create, set, connect, etc.) are
    not supported and will return errors.
    """

    # Commands SendCommand answers; the other query commands are refused
    # with the name of their own RPC (DEDICATED_RPC_FOR_COMMAND).
    READ_ONLY_COMMANDS = {
        'component_exists', 'component_ready', 'pin_has_writer'
    }

    def __init__(self):
        """
        Initialize the service with HAL connection validation.

        Creates a HAL component for introspection access and validates
        that HAL is accessible.

        Raises:
            RuntimeError: If HAL is not accessible.
        """
        self._command_serial = 0
        self._serial_lock = threading.Lock()
        self._component = None

        # Create a HAL component for introspection access
        # HAL requires a component to be created before calling get_info_* functions
        try:
            self._component = hal.component("grpc-hal-service")
            self._component.ready()
            pins = hal.get_info_pins()
            logger.info(f"HalService initialized (CONNECTED) - found {len(pins)} pins")
        except Exception as e:
            raise RuntimeError(f"Failed to access HAL: {e}") from e

        # Module constants, fixed for the life of the process.
        self._type_constants = read_hal_type_constants(hal)
        self._system_info = read_hal_system_info(hal)

    def _next_serial(self) -> int:
        """Get next command serial number, within 1..MAX_RESPONSE_SERIAL."""
        with self._serial_lock:
            self._command_serial = self._command_serial % MAX_RESPONSE_SERIAL + 1
            return self._command_serial

    def _get_hal_data(self):
        """Fetch current HAL data from all introspection APIs."""
        pins = hal.get_info_pins()
        signals = hal.get_info_signals()
        params = hal.get_info_params()
        return pins, signals, params

    def _read_pin_writers(self, pins: List[Dict[str, Any]]) -> Dict[str, bool]:
        """
        Ask HAL, pin by pin, whether the pin's signal has a writer.

        A pin that cannot be asked (it went away since the listing) is left
        out; the mapper then falls back to what the signal list shows.
        """
        pin_writers: Dict[str, bool] = {}
        for pin in pins:
            try:
                name = HalMapper._get(pin, 'name', '')
                # A name that cannot be a key cannot be looked up either.
                hash(name)
            except Exception as e:
                # One odd item must not cost the client the whole listing.
                logger.warning(f"Skipping HAL pin whose name cannot be read ({pin!r}): {e}")
                continue
            try:
                has_writer = hal.pin_has_writer(name)
            except Exception:
                continue
            if isinstance(has_writer, bool):
                pin_writers[name] = has_writer
        return pin_writers

    @staticmethod
    def _items_with_name_matching(
        items: List[Dict[str, Any]], pattern: str
    ) -> List[Dict[str, Any]]:
        """
        The items whose name matches the glob pattern.

        An item whose name cannot be matched (the item is not a dict, or the
        name is not a string) is left out and logged, as the mapper's
        map_each does, so it does not fail the query for the other items.
        """
        matching_items = []
        for item in items:
            try:
                name_matches = fnmatch.fnmatch(HalMapper._get(item, 'name', ''), pattern)
            except Exception as e:
                logger.warning(f"Skipping HAL item whose name cannot be matched ({item!r}): {e}")
                continue
            if name_matches:
                matching_items.append(item)
        return matching_items

    def _current_system_info(self) -> HalSystemInfo:
        """The module constants plus the message level as it is now."""
        return HalSystemInfo(
            message_level=read_hal_message_level(hal),
            is_sim=self._system_info.is_sim,
            is_rt=self._system_info.is_rt,
            is_userspace=self._system_info.is_userspace,
            kernel_version=self._system_info.kernel_version,
        )

    def _new_mapper(
        self,
        pins: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
        params: List[Dict[str, Any]],
        pin_writers: Optional[Dict[str, bool]] = None,
        system_info: Optional[HalSystemInfo] = None,
    ) -> HalMapper:
        """Create a mapper that uses this HAL's type numbers."""
        return HalMapper(
            pins, signals, params,
            type_constants=self._type_constants,
            pin_writers=pin_writers,
            system_info=system_info,
        )

    def _build_system_status(
        self, pin_writers: Optional[Dict[str, bool]] = None
    ) -> hal_pb2.HalSystemStatus:
        """Read everything from HAL and map it to a HalSystemStatus."""
        pins, signals, params = self._get_hal_data()
        if pin_writers is None:
            pin_writers = self._read_pin_writers(pins)
        mapper = self._new_mapper(
            pins, signals, params,
            pin_writers=pin_writers,
            system_info=self._current_system_info(),
        )
        return mapper.map_to_proto()

    # =========================================================================
    # GetSystemStatus RPC
    # =========================================================================

    def GetSystemStatus(
        self,
        request: hal_pb2.GetSystemStatusRequest,
        context: grpc.ServicerContext
    ) -> hal_pb2.HalSystemStatus:
        """Get complete HAL system status."""
        logger.debug("GetSystemStatus called")
        try:
            return self._build_system_status()
        except Exception as e:
            logger.error(f"GetSystemStatus failed: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
            return hal_pb2.HalSystemStatus()

    # =========================================================================
    # SendCommand RPC (Introspection-only)
    # =========================================================================

    def SendCommand(
        self,
        request: hal_pb2.HalCommand,
        context: grpc.ServicerContext
    ) -> hal_pb2.HalCommandResponse:
        """
        Handle HAL commands.

        This service is introspection-only, so modification commands
        return an error. Only component_exists, component_ready and
        pin_has_writer are answered here. The response has no field for a
        value or a list, so get_value and the query_* commands fail with the
        name of the RPC to call instead.
        """
        serial = request.serial or self._next_serial()
        command_type = request.WhichOneof("command")
        logger.debug(f"SendCommand: {command_type} (serial={serial})")

        dedicated_rpc = DEDICATED_RPC_FOR_COMMAND.get(command_type)
        if dedicated_rpc is not None:
            return hal_pb2.HalCommandResponse(
                serial=serial,
                success=False,
                error=(
                    f"Command '{command_type}' returns no result through "
                    f"SendCommand. Call the {dedicated_rpc} RPC instead."
                )
            )

        # Reject modification commands
        if command_type not in self.READ_ONLY_COMMANDS:
            error_msg = (
                f"Command '{command_type}' not supported. "
                f"This service is introspection-only."
            )
            logger.warning(error_msg)
            return hal_pb2.HalCommandResponse(
                serial=serial,
                success=False,
                error=error_msg
            )

        # Handle read-only commands
        try:
            if command_type == 'component_exists':
                name = request.component_exists.name
                exists = hal.component_exists(name)
                return hal_pb2.HalCommandResponse(
                    serial=serial,
                    success=True,
                    error="" if exists else f"Component '{name}' does not exist"
                )
            elif command_type == 'component_ready':
                name = request.component_ready.name
                if hal.component_exists(name):
                    ready = hal.component_is_ready(name)
                    return hal_pb2.HalCommandResponse(
                        serial=serial,
                        success=True,
                        error="" if ready else f"Component '{name}' is not ready"
                    )
                else:
                    return hal_pb2.HalCommandResponse(
                        serial=serial,
                        success=False,
                        error=f"Component '{name}' does not exist"
                    )
            else:
                name = request.pin_has_writer.name
                has_writer = hal.pin_has_writer(name)
                return hal_pb2.HalCommandResponse(
                    serial=serial,
                    success=True,
                    error="" if has_writer else f"Pin '{name}' has no writer"
                )
        except Exception as e:
            logger.error(f"SendCommand error for {command_type}: {e}")
            return hal_pb2.HalCommandResponse(
                serial=serial,
                success=False,
                error=str(e)
            )

    # =========================================================================
    # GetValue RPC
    # =========================================================================

    def GetValue(
        self,
        request: hal_pb2.GetValueCommand,
        context: grpc.ServicerContext
    ) -> hal_pb2.GetValueResponse:
        """Get value of a pin, signal, or parameter."""
        logger.debug(f"GetValue called - name={request.name}")

        try:
            value = hal.get_value(request.name)
            mapper = self._new_mapper(*self._get_hal_data())
            # Where HAL lists no type for the name (a parameter in
            # LinuxCNC 2.9), the type is worked out from the value just read.
            value_type, hal_value = mapper.describe_value(
                value, mapper.declared_types().get(request.name)
            )

            return hal_pb2.GetValueResponse(
                serial=self._next_serial(),
                success=True,
                error="",
                value=hal_value,
                type=value_type
            )
        except Exception as e:
            logger.error(f"GetValue failed for {request.name}: {e}")
            return hal_pb2.GetValueResponse(
                serial=self._next_serial(),
                success=False,
                error=str(e),
                value=hal_pb2.HalValue(),
                type=hal_pb2.HAL_TYPE_UNSPECIFIED
            )

    # =========================================================================
    # QueryPins RPC
    # =========================================================================

    def QueryPins(
        self,
        request: hal_pb2.QueryPinsCommand,
        context: grpc.ServicerContext
    ) -> hal_pb2.QueryPinsResponse:
        """Query pins matching a pattern."""
        pattern = request.pattern or "*"
        logger.debug(f"QueryPins called - pattern={pattern}")

        try:
            all_pins = hal.get_info_pins()

            if pattern == "*":
                matching = all_pins
            else:
                matching = self._items_with_name_matching(all_pins, pattern)

            mapper = self._new_mapper(
                all_pins, [], [], pin_writers=self._read_pin_writers(matching)
            )
            pins = mapper.map_each(mapper.map_pin, matching)

            return hal_pb2.QueryPinsResponse(
                serial=self._next_serial(),
                success=True,
                error="",
                pins=pins
            )
        except Exception as e:
            logger.error(f"QueryPins failed: {e}")
            return hal_pb2.QueryPinsResponse(
                serial=self._next_serial(),
                success=False,
                error=str(e),
                pins=[]
            )

    # =========================================================================
    # QuerySignals RPC
    # =========================================================================

    def QuerySignals(
        self,
        request: hal_pb2.QuerySignalsCommand,
        context: grpc.ServicerContext
    ) -> hal_pb2.QuerySignalsResponse:
        """Query signals matching a pattern."""
        pattern = request.pattern or "*"
        logger.debug(f"QuerySignals called - pattern={pattern}")

        try:
            all_pins = hal.get_info_pins()
            all_signals = hal.get_info_signals()
            mapper = self._new_mapper(all_pins, all_signals, [])

            if pattern == "*":
                matching = all_signals
            else:
                matching = self._items_with_name_matching(all_signals, pattern)

            signals = mapper.map_each(mapper.map_signal, matching)

            return hal_pb2.QuerySignalsResponse(
                serial=self._next_serial(),
                success=True,
                error="",
                signals=signals
            )
        except Exception as e:
            logger.error(f"QuerySignals failed: {e}")
            return hal_pb2.QuerySignalsResponse(
                serial=self._next_serial(),
                success=False,
                error=str(e),
                signals=[]
            )

    # =========================================================================
    # QueryParams RPC
    # =========================================================================

    def QueryParams(
        self,
        request: hal_pb2.QueryParamsCommand,
        context: grpc.ServicerContext
    ) -> hal_pb2.QueryParamsResponse:
        """Query parameters matching a pattern."""
        pattern = request.pattern or "*"
        logger.debug(f"QueryParams called - pattern={pattern}")

        try:
            all_params = hal.get_info_params()
            mapper = self._new_mapper([], [], all_params)

            if pattern == "*":
                matching = all_params
            else:
                matching = self._items_with_name_matching(all_params, pattern)

            params = mapper.map_each(mapper.map_param, matching)

            return hal_pb2.QueryParamsResponse(
                serial=self._next_serial(),
                success=True,
                error="",
                params=params
            )
        except Exception as e:
            logger.error(f"QueryParams failed: {e}")
            return hal_pb2.QueryParamsResponse(
                serial=self._next_serial(),
                success=False,
                error=str(e),
                params=[]
            )

    # =========================================================================
    # QueryComponents RPC
    # =========================================================================

    def QueryComponents(
        self,
        request: hal_pb2.QueryComponentsCommand,
        context: grpc.ServicerContext
    ) -> hal_pb2.QueryComponentsResponse:
        """
        Query components matching a pattern.

        The hal module has no component list, so components are inferred
        from pin and parameter names (the part before the last dot). Only
        name, pins and params are filled in; ready, type and pid are not known
        and stay at their defaults.
        """
        pattern = request.pattern or "*"
        logger.debug(f"QueryComponents called - pattern={pattern}")

        try:
            pins, signals, params = self._get_hal_data()
            mapper = self._new_mapper(pins, signals, params)

            # Get all components from the mapper's derived list
            all_components = list(mapper._components.values())

            if pattern == "*":
                matching = all_components
            else:
                matching = self._items_with_name_matching(all_components, pattern)

            components = mapper.map_each(mapper.map_component, matching)

            return hal_pb2.QueryComponentsResponse(
                serial=self._next_serial(),
                success=True,
                error="",
                components=components
            )
        except Exception as e:
            logger.error(f"QueryComponents failed: {e}")
            return hal_pb2.QueryComponentsResponse(
                serial=self._next_serial(),
                success=False,
                error=str(e),
                components=[]
            )

    # =========================================================================
    # StreamStatus RPC
    # =========================================================================

    def StreamStatus(
        self,
        request: hal_pb2.HalStreamStatusRequest,
        context: grpc.ServicerContext
    ) -> Iterator[hal_pb2.HalSystemStatus]:
        """
        Stream HAL system status updates.

        interval_ms of 0 means 100 ms; an interval above 60 s is clamped to
        60 s. The wait between polls ends at once when the client cancels or
        disconnects. has_writer is refreshed every 2 s, not on every poll.
        """
        interval = stream_interval_seconds(request.interval_ms)
        cancelled = watch_for_cancellation(context)
        logger.info(f"StreamStatus started - interval={interval}s")

        consecutive_errors = 0
        abort_details = None
        pin_writers: Dict[str, bool] = {}
        pin_writers_read_at = None

        try:
            while not cancelled.is_set() and context.is_active():
                try:
                    if (
                        pin_writers_read_at is None
                        or time.monotonic() - pin_writers_read_at >= PIN_WRITERS_REFRESH_SECONDS
                    ):
                        pin_writers = self._read_pin_writers(hal.get_info_pins())
                        pin_writers_read_at = time.monotonic()
                    status = self._build_system_status(pin_writers)
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(
                        f"StreamStatus poll error "
                        f"({consecutive_errors}/{MAX_CONSECUTIVE_POLL_ERRORS}): {e}"
                    )
                    if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                        abort_details = f"Too many consecutive errors: {e}"
                        break
                else:
                    consecutive_errors = 0
                    yield status
                cancelled.wait(interval)
        finally:
            logger.info("StreamStatus ended")

        # Outside the try: abort raises, and nothing may catch that and
        # replace the status with another one.
        if abort_details is not None:
            context.abort(grpc.StatusCode.INTERNAL, abort_details)
            return

    # =========================================================================
    # WatchValues RPC
    # =========================================================================

    def WatchValues(
        self,
        request: hal_pb2.WatchRequest,
        context: grpc.ServicerContext
    ) -> Iterator[hal_pb2.ValueChangeBatch]:
        """
        Watch for value changes using polling-based change detection.

        Names are de-duplicated; more than MAX_WATCHED_NAMES distinct names
        is INVALID_ARGUMENT, as is a name that cannot be read at the start.
        interval_ms of 0 means 100 ms; an interval above 60 s is clamped to
        60 s. The wait between polls ends at once when the client cancels or
        disconnects. If a watched name cannot be read for
        MAX_CONSECUTIVE_POLL_ERRORS polls in a row (it was removed, say), the
        stream ends with INTERNAL and names it; changes of the other names
        are still sent until then. Two NaN readings count as unchanged.
        """
        interval = stream_interval_seconds(request.interval_ms)
        names = list(dict.fromkeys(request.names))

        if not names:
            logger.warning("WatchValues called with no names to watch")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("At least one name must be specified")
            return

        if len(names) > MAX_WATCHED_NAMES:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Too many names to watch: {len(names)} (the limit is {MAX_WATCHED_NAMES})"
            )
            return

        cancelled = watch_for_cancellation(context)
        logger.info(f"WatchValues started - {len(names)} names, interval={interval}s")

        # One read of the HAL lists gives the type of every watched name.
        try:
            mapper = self._new_mapper(*self._get_hal_data())
            declared_types = mapper.declared_types()
        except Exception as e:
            logger.error(f"WatchValues could not read HAL: {e}")
            context.abort(grpc.StatusCode.INTERNAL, f"Cannot read HAL: {e}")
            return

        # Initialize with current values - fail fast on invalid names
        previous_values: Dict[str, Any] = {}
        for name in names:
            if cancelled.is_set() or not context.is_active():
                return
            try:
                previous_values[name] = hal.get_value(name)
            except Exception as e:
                logger.error(f"Invalid watch target '{name}': {e}")
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"Cannot watch '{name}': {e}")
                return

        consecutive_errors = 0
        abort_details = None

        try:
            while not cancelled.is_set() and context.is_active():
                changes = []
                poll_error = None

                for name in names:
                    try:
                        current = hal.get_value(name)
                        previous = previous_values[name]

                        if values_differ(previous, current):
                            hal_type = declared_types.get(name)
                            if hal_type is None:
                                hal_type = mapper.infer_type_from_values(previous, current)
                            changes.append(hal_pb2.ValueChange(
                                timestamp=int(time.time() * 1e9),
                                name=name,
                                old_value=mapper.map_value(previous, hal_type),
                                new_value=mapper.map_value(current, hal_type)
                            ))

                        previous_values[name] = current
                    except Exception as e:
                        logger.warning(f"Error polling {name}: {e}")
                        if poll_error is None:
                            poll_error = f"cannot read '{name}': {e}"

                if changes:
                    yield hal_pb2.ValueChangeBatch(
                        timestamp=int(time.time() * 1e9),
                        changes=changes
                    )

                if poll_error is None:
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                        abort_details = f"Too many consecutive errors: {poll_error}"
                        break

                cancelled.wait(interval)
        finally:
            logger.info("WatchValues ended")

        # Outside the try: abort raises, and nothing may catch that and
        # replace the status with another one.
        if abort_details is not None:
            context.abort(grpc.StatusCode.INTERNAL, abort_details)
            return
