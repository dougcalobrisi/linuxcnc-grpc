"""
HalService gRPC implementation.

Provides HAL introspection via gRPC. This is READ-ONLY introspection -
no component creation or modification is supported.
"""

import time
import logging
import fnmatch
from typing import Iterator, Dict, Any

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
from .hal_mapper import HalMapper

logger = logging.getLogger(__name__)


class HalServiceServicer(hal_pb2_grpc.HalServiceServicer):
    """
    gRPC servicer for HAL introspection operations.

    This service provides READ-ONLY access to HAL pins, signals, parameters,
    and components. Modification commands (create, set, connect, etc.) are
    not supported and will return errors.
    """

    # Commands allowed in introspection-only mode
    READ_ONLY_COMMANDS = {
        'get_value', 'query_pins', 'query_signals', 'query_params',
        'query_components', 'component_exists', 'component_ready', 'pin_has_writer'
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

    def _next_serial(self) -> int:
        """Get next command serial number."""
        self._command_serial += 1
        return self._command_serial

    def _get_hal_data(self):
        """Fetch current HAL data from all introspection APIs."""
        pins = hal.get_info_pins()
        signals = hal.get_info_signals()
        params = hal.get_info_params()
        return pins, signals, params

    def _get_type_for_name(self, name: str) -> int:
        """Look up HAL type for a pin, signal, or param by name."""
        # Check pins first (HAL uses UPPERCASE keys)
        for pin in hal.get_info_pins():
            if HalMapper._get(pin, 'name') == name:
                return HalMapper._get(pin, 'type', HalMapper.HAL_FLOAT)
        # Check signals
        for sig in hal.get_info_signals():
            if HalMapper._get(sig, 'name') == name:
                return HalMapper._get(sig, 'type', HalMapper.HAL_FLOAT)
        # Check params (no TYPE field, infer from value)
        for param in hal.get_info_params():
            if HalMapper._get(param, 'name') == name:
                value = HalMapper._get(param, 'value', 0)
                return HalMapper([], [], [])._infer_type_from_value(value)
        return HalMapper.HAL_FLOAT

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
            pins, signals, params = self._get_hal_data()
            mapper = HalMapper(pins, signals, params)
            status = mapper.map_to_proto()
            return status
        except Exception as e:
            logger.error(f"GetSystemStatus failed: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

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
        will return an error. Query commands are supported but should
        preferably use the dedicated Query* RPCs.
        """
        serial = request.serial or self._next_serial()
        command_type = request.WhichOneof("command")
        logger.debug(f"SendCommand: {command_type} (serial={serial})")

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
            elif command_type == 'pin_has_writer':
                name = request.pin_has_writer.name
                has_writer = hal.pin_has_writer(name)
                return hal_pb2.HalCommandResponse(
                    serial=serial,
                    success=True,
                    error="" if has_writer else f"Pin '{name}' has no writer"
                )
            else:
                # Other read-only commands should use dedicated RPCs
                return hal_pb2.HalCommandResponse(
                    serial=serial,
                    success=True,
                    error=""
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
            hal_type = self._get_type_for_name(request.name)

            # Create mapper for value conversion
            mapper = HalMapper([], [], [])
            hal_value = mapper.map_value(value, hal_type)

            return hal_pb2.GetValueResponse(
                serial=self._next_serial(),
                success=True,
                error="",
                value=hal_value,
                type=mapper._map_hal_type(hal_type)
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
            mapper = HalMapper(all_pins, [], [])

            if pattern == "*":
                matching = all_pins
            else:
                matching = [p for p in all_pins
                           if fnmatch.fnmatch(HalMapper._get(p, 'name', ''), pattern)]

            pins = [mapper.map_pin(p) for p in matching]

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
            mapper = HalMapper(all_pins, all_signals, [])

            if pattern == "*":
                matching = all_signals
            else:
                matching = [s for s in all_signals
                           if fnmatch.fnmatch(HalMapper._get(s, 'name', ''), pattern)]

            signals = [mapper.map_signal(s) for s in matching]

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
            mapper = HalMapper([], [], all_params)

            if pattern == "*":
                matching = all_params
            else:
                matching = [p for p in all_params
                           if fnmatch.fnmatch(HalMapper._get(p, 'name', ''), pattern)]

            params = [mapper.map_param(p) for p in matching]

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
        """Query components matching a pattern."""
        pattern = request.pattern or "*"
        logger.debug(f"QueryComponents called - pattern={pattern}")

        try:
            pins, signals, params = self._get_hal_data()
            mapper = HalMapper(pins, signals, params)

            # Get all components from the mapper's derived list
            all_components = list(mapper._components.values())

            if pattern == "*":
                matching = all_components
            else:
                matching = [c for c in all_components
                           if fnmatch.fnmatch(c.get('name', ''), pattern)]

            components = [mapper.map_component(c) for c in matching]

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
        """Stream HAL system status updates."""
        interval_ms = request.interval_ms if request.interval_ms > 0 else 100
        interval = interval_ms / 1000.0
        logger.info(f"StreamStatus started - interval={interval_ms}ms")

        consecutive_errors = 0
        max_consecutive_errors = 5

        try:
            while context.is_active():
                try:
                    pins, signals, params = self._get_hal_data()
                    mapper = HalMapper(pins, signals, params)
                    status = mapper.map_to_proto()
                    yield status
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"StreamStatus poll error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                    if consecutive_errors >= max_consecutive_errors:
                        context.abort(grpc.StatusCode.INTERNAL, f"Too many consecutive errors: {e}")
                        return
                time.sleep(interval)
        except grpc.RpcError:
            pass  # Client disconnected; normal shutdown
        except Exception as e:
            logger.error(f"StreamStatus error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, f"Stream failed: {e}")
        finally:
            logger.info("StreamStatus ended")

    # =========================================================================
    # WatchValues RPC
    # =========================================================================

    def WatchValues(
        self,
        request: hal_pb2.WatchRequest,
        context: grpc.ServicerContext
    ) -> Iterator[hal_pb2.ValueChangeBatch]:
        """Watch for value changes using polling-based change detection."""
        interval_ms = request.interval_ms if request.interval_ms > 0 else 100
        interval = interval_ms / 1000.0
        names = list(request.names) if request.names else []

        if not names:
            logger.warning("WatchValues called with no names to watch")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("At least one name must be specified")
            return

        logger.info(f"WatchValues started - names={names}, interval={interval_ms}ms")

        # Track previous values for change detection
        previous_values: Dict[str, Any] = {}
        previous_types: Dict[str, int] = {}

        # Initialize with current values - fail fast on invalid names
        for name in names:
            try:
                previous_values[name] = hal.get_value(name)
                previous_types[name] = self._get_type_for_name(name)
            except Exception as e:
                logger.error(f"Invalid watch target '{name}': {e}")
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"Cannot watch '{name}': {e}")
                return

        # Create mapper for value conversion
        mapper = HalMapper([], [], [])

        try:
            while context.is_active():
                changes = []

                for name in names:
                    try:
                        current = hal.get_value(name)
                        hal_type = previous_types.get(name, HalMapper.HAL_FLOAT)

                        if name in previous_values and current != previous_values[name]:
                            changes.append(hal_pb2.ValueChange(
                                timestamp=int(time.time() * 1e9),
                                name=name,
                                old_value=mapper.map_value(previous_values[name], hal_type),
                                new_value=mapper.map_value(current, hal_type)
                            ))

                        previous_values[name] = current
                    except Exception as e:
                        logger.warning(f"Error polling {name}: {e}")

                if changes:
                    yield hal_pb2.ValueChangeBatch(
                        timestamp=int(time.time() * 1e9),
                        changes=changes
                    )

                time.sleep(interval)
        except grpc.RpcError:
            pass  # Client disconnected; normal shutdown
        except Exception as e:
            logger.error(f"WatchValues error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, f"Stream failed: {e}")
        finally:
            logger.info("WatchValues ended")
