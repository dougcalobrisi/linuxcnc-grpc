"""
Unit tests for HalServiceServicer.

Tests the gRPC service layer for HAL introspection.
"""

import math
import sys
import threading
from unittest.mock import MagicMock, patch

import grpc
import pytest

# Longest a test waits for a stream thread that should already have ended.
STREAM_END_TIMEOUT_SECONDS = 5.0


def collect_stream_in_thread(stream, on_item=None):
    """Consume a response stream in a daemon thread; returns (thread, items)."""
    received_items = []

    def consume():
        for item in stream:
            received_items.append(item)
            if on_item is not None:
                on_item(item)

    consumer_thread = threading.Thread(target=consume, daemon=True)
    consumer_thread.start()
    return consumer_thread, received_items


class TestHalServiceInit:
    """Test HalServiceServicer initialization."""

    def test_init_success(self, mock_hal_module):
        """Service initializes with valid HAL connection."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            service = HalServiceServicer()
            assert service._component is not None
            mock_hal_module.component.assert_called_once_with("grpc-hal-service")

    def test_init_failure(self, mock_hal_module):
        """Service raises RuntimeError when HAL fails."""
        mock_hal_module.component.side_effect = Exception("HAL not running")
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            with pytest.raises(RuntimeError, match="Failed to access HAL"):
                HalServiceServicer()


class TestGetSystemStatus:
    """Test GetSystemStatus RPC."""

    def test_success(self, mock_hal_module, mock_grpc_context):
        """Returns complete HAL system status."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.GetSystemStatusRequest()
            response = service.GetSystemStatus(request, mock_grpc_context)

            assert response.timestamp > 0
            assert len(response.pins) == 3
            assert len(response.signals) == 2
            assert len(response.params) == 3

    def test_malformed_pin_and_param_are_left_out_of_the_status(
        self, mock_hal_module, mock_hal_pins, mock_hal_params, mock_grpc_context, caplog
    ):
        """One item HAL lists without a usable name does not fail the RPC."""
        mock_hal_module.get_info_pins.return_value = [
            {"NAME": None, "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0}
        ] + mock_hal_pins
        mock_hal_module.get_info_params.return_value = mock_hal_params + [
            {"NAME": None, "DIRECTION": 192, "VALUE": 1.0}
        ]
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_mapper"):
                response = service.GetSystemStatus(
                    hal_pb2.GetSystemStatusRequest(), mock_grpc_context
                )

            mock_grpc_context.abort.assert_not_called()
            assert [pin.name for pin in response.pins] == [
                pin["NAME"] for pin in mock_hal_pins
            ]
            assert [param.name for param in response.params] == [
                param["NAME"] for param in mock_hal_params
            ]
            assert len(response.signals) == 2
            assert "axis.x" in [component.name for component in response.components]
            assert "could not be grouped into a component" in caplog.text

    def test_exception_handling(self, mock_hal_module, mock_grpc_context):
        """Sets error code on exception."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_pins.side_effect = Exception("HAL error")
            request = hal_pb2.GetSystemStatusRequest()
            response = service.GetSystemStatus(request, mock_grpc_context)

            mock_grpc_context.abort.assert_called_once()
            args = mock_grpc_context.abort.call_args
            assert args[0][0] == grpc.StatusCode.INTERNAL
            # The mock abort does not raise; the RPC must still return a message.
            assert isinstance(response, hal_pb2.HalSystemStatus)

    def test_reports_system_flags_from_the_hal_module(self, mock_hal_module, mock_grpc_context):
        """is_sim, is_rt, is_userspace, kernel_version and the level are read, not assumed."""
        mock_hal_module.is_sim = 1
        mock_hal_module.is_rt = 0
        mock_hal_module.is_userspace = 1
        mock_hal_module.kernel_version = "Not Available"
        mock_hal_module.MSG_NONE = 0
        mock_hal_module.MSG_ERR = 1
        mock_hal_module.MSG_WARN = 2
        mock_hal_module.MSG_INFO = 3
        mock_hal_module.MSG_DBG = 4
        mock_hal_module.MSG_ALL = 5
        mock_hal_module.get_msg_level.return_value = 2
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )

            assert response.is_sim is True
            assert response.is_rt is False
            assert response.is_userspace is True
            assert response.kernel_version == "Not Available"
            assert response.message_level == hal_pb2.MSG_WARN

            # The level can change while the server runs; the flags cannot.
            mock_hal_module.get_msg_level.return_value = 4
            response = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )
            assert response.message_level == hal_pb2.MSG_DBG

    def test_system_flags_unknown_when_the_hal_module_lacks_them(
        self, mock_hal_module, mock_grpc_context
    ):
        """Attributes that are not real values are reported as "not known"."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )

            assert response.is_sim is False
            assert response.is_rt is False
            assert response.is_userspace is False
            assert response.kernel_version == ""
            assert response.message_level == hal_pb2.MSG_LEVEL_UNSPECIFIED

    def test_u32_param_above_s32_range_does_not_abort(
        self, mock_hal_module, mock_hal_params, mock_grpc_context
    ):
        """A u32 parameter above 2147483647 is part of the status, not an INTERNAL error."""
        mock_hal_module.get_info_params.return_value = mock_hal_params + [
            {"NAME": "hm2.0.watchdog.timeout_ns", "DIRECTION": 192, "VALUE": 4000000000},
        ]
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )

            mock_grpc_context.abort.assert_not_called()
            assert response.params[-1].type == hal_pb2.HAL_U32
            assert response.params[-1].value.u32_value == 4000000000

    def test_has_writer_is_what_hal_says(self, mock_hal_module, mock_grpc_context):
        """has_writer comes from hal.pin_has_writer, not from the pin direction."""
        pins_with_writer = {"iocontrol.0.emc-enable-in"}
        mock_hal_module.pin_has_writer.side_effect = (
            lambda pin_name: pin_name in pins_with_writer
        )
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )

            has_writer_by_name = {pin.name: pin.has_writer for pin in response.pins}
            assert has_writer_by_name == {
                "axis.x.pos-cmd": False,  # HAL_OUT, not linked to a signal
                "spindle.0.speed-out": False,
                "iocontrol.0.emc-enable-in": True,  # HAL_IN on a driven signal
            }

    def test_components_do_not_claim_unknown_facts(self, mock_hal_module, mock_grpc_context):
        """Inferred components carry names and members only."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )

            assert len(response.components) > 0
            for component in response.components:
                assert component.ready is False
                assert component.type == 0
                assert component.pid == 0


class TestSendCommand:
    """Test SendCommand RPC."""

    def test_rejects_modification_command(self, mock_hal_module, mock_grpc_context):
        """Rejects commands not in READ_ONLY_COMMANDS."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                serial=1,
                set_pin=hal_pb2.SetPinValueCommand(name="test", value=hal_pb2.HalValue())
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is False
            assert "introspection-only" in response.error

    def test_component_exists_true(self, mock_hal_module, mock_grpc_context):
        """Returns success when component exists."""
        mock_hal_module.component_exists.return_value = True
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                component_exists=hal_pb2.ComponentExistsCommand(name="motion")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is True
            assert response.error == ""

    def test_component_exists_false(self, mock_hal_module, mock_grpc_context):
        """Returns error message when component doesn't exist."""
        mock_hal_module.component_exists.return_value = False
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                component_exists=hal_pb2.ComponentExistsCommand(name="missing")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is True
            assert "does not exist" in response.error

    def test_component_ready_true(self, mock_hal_module, mock_grpc_context):
        """Returns success when component is ready."""
        mock_hal_module.component_exists.return_value = True
        mock_hal_module.component_is_ready.return_value = True
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                component_ready=hal_pb2.ComponentReadyCommand(name="motion")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is True
            assert response.error == ""

    def test_component_ready_not_ready(self, mock_hal_module, mock_grpc_context):
        """Returns error message when component is not ready."""
        mock_hal_module.component_exists.return_value = True
        mock_hal_module.component_is_ready.return_value = False
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                component_ready=hal_pb2.ComponentReadyCommand(name="not-ready")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is True
            assert "is not ready" in response.error

    def test_component_ready_not_exists(self, mock_hal_module, mock_grpc_context):
        """Returns failure when component doesn't exist."""
        mock_hal_module.component_exists.return_value = False
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                component_ready=hal_pb2.ComponentReadyCommand(name="missing")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is False
            assert "does not exist" in response.error

    def test_pin_has_writer_true(self, mock_hal_module, mock_grpc_context):
        """Returns success when pin has writer."""
        mock_hal_module.pin_has_writer.return_value = True
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                pin_has_writer=hal_pb2.PinHasWriterCommand(name="axis.x.pos-cmd")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is True
            assert response.error == ""

    def test_pin_has_writer_false(self, mock_hal_module, mock_grpc_context):
        """Returns error message when pin has no writer."""
        mock_hal_module.pin_has_writer.return_value = False
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                pin_has_writer=hal_pb2.PinHasWriterCommand(name="axis.x.pos-cmd")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is True
            assert "has no writer" in response.error

    @pytest.mark.parametrize("command_field,command_message,rpc_name", [
        ("get_value", "GetValueCommand", "GetValue"),
        ("query_pins", "QueryPinsCommand", "QueryPins"),
        ("query_signals", "QuerySignalsCommand", "QuerySignals"),
        ("query_params", "QueryParamsCommand", "QueryParams"),
        ("query_components", "QueryComponentsCommand", "QueryComponents"),
    ])
    def test_query_commands_fail_and_name_their_rpc(
        self, mock_hal_module, mock_grpc_context, command_field, command_message, rpc_name
    ):
        """A command whose result the response cannot carry is not a silent success."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                serial=7, **{command_field: getattr(hal_pb2, command_message)()}
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is False
            assert response.serial == 7
            assert f"{rpc_name} RPC" in response.error

    def test_command_without_a_command_fails(self, mock_hal_module, mock_grpc_context):
        """An empty HalCommand is refused."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.SendCommand(hal_pb2.HalCommand(), mock_grpc_context)

            assert response.success is False

    def test_uses_provided_serial(self, mock_hal_module, mock_grpc_context):
        """Uses serial from request."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                serial=999,
                component_exists=hal_pb2.ComponentExistsCommand(name="test")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.serial == 999

    def test_auto_increments_serial(self, mock_hal_module, mock_grpc_context):
        """Auto-increments serial when not provided."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request1 = hal_pb2.HalCommand(
                component_exists=hal_pb2.ComponentExistsCommand(name="test")
            )
            request2 = hal_pb2.HalCommand(
                component_exists=hal_pb2.ComponentExistsCommand(name="test")
            )
            response1 = service.SendCommand(request1, mock_grpc_context)
            response2 = service.SendCommand(request2, mock_grpc_context)

            assert response2.serial == response1.serial + 1

    def test_serial_counter_wraps_to_one_after_the_largest_int32(
        self, mock_hal_module, mock_grpc_context
    ):
        """The server's own counter must fit the int32 serial fields."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            largest_int32 = 2**31 - 1
            service = HalServiceServicer()
            service._command_serial = largest_int32 - 1

            response_serials = [
                service.SendCommand(
                    hal_pb2.HalCommand(
                        component_exists=hal_pb2.ComponentExistsCommand(name="test")
                    ),
                    mock_grpc_context,
                ).serial
                for _ in range(3)
            ]

            assert response_serials == [largest_int32, 1, 2]

    def test_command_exception_handling(self, mock_hal_module, mock_grpc_context):
        """Returns error when command raises exception."""
        mock_hal_module.component_exists.side_effect = Exception("HAL error")
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalCommand(
                component_exists=hal_pb2.ComponentExistsCommand(name="test")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.success is False
            assert "HAL error" in response.error


class TestGetValue:
    """Test GetValue RPC."""

    def test_returns_float_value(self, mock_hal_module, mock_grpc_context):
        """Returns float value correctly."""
        mock_hal_module.get_value.return_value = 123.456
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.GetValueCommand(name="axis.x.pos-cmd")
            response = service.GetValue(request, mock_grpc_context)

            assert response.success is True
            assert abs(response.value.float_value - 123.456) < 0.001

    def test_returns_bit_value(self, mock_hal_module, mock_grpc_context):
        """Returns bit value correctly."""
        mock_hal_module.get_value.return_value = True
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.GetValueCommand(name="iocontrol.0.emc-enable-in")
            response = service.GetValue(request, mock_grpc_context)

            assert response.success is True
            assert response.value.bit_value is True

    @pytest.mark.parametrize("value", [2**31, 4000000000, 2**32 - 1])
    def test_returns_u32_param_above_s32_range(
        self, mock_hal_module, mock_hal_params, mock_grpc_context, value
    ):
        """A u32 parameter above 2147483647 is returned as u32, not an error."""
        mock_hal_module.get_info_params.return_value = mock_hal_params + [
            {"NAME": "hm2.0.watchdog.timeout_ns", "DIRECTION": 192, "VALUE": 5},
        ]
        mock_hal_module.get_value.return_value = value
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.GetValueCommand(name="hm2.0.watchdog.timeout_ns")
            response = service.GetValue(request, mock_grpc_context)

            assert response.success is True, response.error
            assert response.type == hal_pb2.HAL_U32
            assert response.value.u32_value == value

    def test_uses_the_listed_type_of_a_pin(self, mock_hal_module, mock_grpc_context):
        """A u32 pin with a small value is u32 because the pin list says so."""
        mock_hal_module.get_info_pins.return_value = [
            {"NAME": "encoder.0.rawcounts", "TYPE": 4, "DIRECTION": 32, "VALUE": 12},
        ]
        mock_hal_module.get_value.return_value = 12
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.GetValueCommand(name="encoder.0.rawcounts")
            response = service.GetValue(request, mock_grpc_context)

            assert response.success is True
            assert response.type == hal_pb2.HAL_U32
            assert response.value.u32_value == 12

    def test_error_on_missing_value(self, mock_hal_module, mock_grpc_context):
        """Returns error when value lookup fails."""
        mock_hal_module.get_value.side_effect = Exception("Pin not found")
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.GetValueCommand(name="nonexistent")
            response = service.GetValue(request, mock_grpc_context)

            assert response.success is False
            assert "Pin not found" in response.error


class TestQueryPins:
    """Test QueryPins RPC."""

    def test_returns_all_pins(self, mock_hal_module, mock_grpc_context):
        """Returns all pins with wildcard pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryPinsCommand(pattern="*")
            response = service.QueryPins(request, mock_grpc_context)

            assert response.success is True
            assert len(response.pins) == 3

    def test_filters_by_pattern(self, mock_hal_module, mock_grpc_context):
        """Filters pins by glob pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryPinsCommand(pattern="axis.*")
            response = service.QueryPins(request, mock_grpc_context)

            assert response.success is True
            assert len(response.pins) == 1
            assert response.pins[0].name == "axis.x.pos-cmd"

    def test_returns_empty_for_no_match(self, mock_hal_module, mock_grpc_context):
        """Returns empty list when no pins match."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryPinsCommand(pattern="nonexistent.*")
            response = service.QueryPins(request, mock_grpc_context)

            assert response.success is True
            assert len(response.pins) == 0

    def test_error_handling(self, mock_hal_module, mock_grpc_context):
        """Returns error on exception."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_pins.side_effect = Exception("HAL error")
            request = hal_pb2.QueryPinsCommand(pattern="*")
            response = service.QueryPins(request, mock_grpc_context)

            assert response.success is False
            assert "HAL error" in response.error

    def test_has_writer_is_asked_only_for_matching_pins(
        self, mock_hal_module, mock_grpc_context
    ):
        """Each has_writer answer walks HAL's pin list, so only listed pins are asked."""
        mock_hal_module.pin_has_writer.return_value = False
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryPinsCommand(pattern="axis.*")
            response = service.QueryPins(request, mock_grpc_context)

            mock_hal_module.pin_has_writer.assert_called_once_with("axis.x.pos-cmd")
            assert response.pins[0].direction == hal_pb2.HAL_OUT
            assert response.pins[0].has_writer is False

    def test_pin_that_vanished_before_has_writer_is_still_listed(
        self, mock_hal_module, mock_grpc_context
    ):
        """A pin removed between the listing and the has_writer question does not fail the query."""
        mock_hal_module.pin_has_writer.side_effect = NameError("Pin does not exist")
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.QueryPins(hal_pb2.QueryPinsCommand(), mock_grpc_context)

            assert response.success is True
            assert len(response.pins) == 3
            assert all(pin.has_writer is False for pin in response.pins)

    def test_default_pattern(self, mock_hal_module, mock_grpc_context):
        """Uses wildcard pattern when not specified."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryPinsCommand()
            response = service.QueryPins(request, mock_grpc_context)

            assert response.success is True
            assert len(response.pins) == 3


class TestQuerySignals:
    """Test QuerySignals RPC."""

    def test_returns_all_signals(self, mock_hal_module, mock_grpc_context):
        """Returns all signals with wildcard pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QuerySignalsCommand(pattern="*")
            response = service.QuerySignals(request, mock_grpc_context)

            assert response.success is True
            assert len(response.signals) == 2

    def test_filters_by_pattern(self, mock_hal_module, mock_grpc_context):
        """Filters signals by glob pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QuerySignalsCommand(pattern="x-*")
            response = service.QuerySignals(request, mock_grpc_context)

            assert response.success is True
            assert len(response.signals) == 1

    def test_error_handling(self, mock_hal_module, mock_grpc_context):
        """Returns error on exception."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_signals.side_effect = Exception("HAL error")
            request = hal_pb2.QuerySignalsCommand(pattern="*")
            response = service.QuerySignals(request, mock_grpc_context)

            assert response.success is False
            assert "HAL error" in response.error


class TestQueryParams:
    """Test QueryParams RPC."""

    def test_returns_all_params(self, mock_hal_module, mock_grpc_context):
        """Returns all parameters with wildcard pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryParamsCommand(pattern="*")
            response = service.QueryParams(request, mock_grpc_context)

            assert response.success is True
            assert len(response.params) == 3

    def test_filters_by_pattern(self, mock_hal_module, mock_grpc_context):
        """Filters params by glob pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryParamsCommand(pattern="pid.*")
            response = service.QueryParams(request, mock_grpc_context)

            assert response.success is True
            assert len(response.params) == 1

    def test_error_handling(self, mock_hal_module, mock_grpc_context):
        """Returns error on exception."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_params.side_effect = Exception("HAL error")
            request = hal_pb2.QueryParamsCommand(pattern="*")
            response = service.QueryParams(request, mock_grpc_context)

            assert response.success is False
            assert "HAL error" in response.error


    def test_u32_param_above_s32_range(self, mock_hal_module, mock_hal_params, mock_grpc_context):
        """A u32 parameter above 2147483647 does not fail the whole query."""
        mock_hal_module.get_info_params.return_value = mock_hal_params + [
            {"NAME": "hm2.0.watchdog.timeout_ns", "DIRECTION": 192, "VALUE": 2**32 - 1},
        ]
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.QueryParams(hal_pb2.QueryParamsCommand(), mock_grpc_context)

            assert response.success is True, response.error
            assert len(response.params) == 4
            assert response.params[-1].type == hal_pb2.HAL_U32
            assert response.params[-1].value.u32_value == 2**32 - 1


class TestQueryComponents:
    """Test QueryComponents RPC."""

    def test_returns_all_components(self, mock_hal_module, mock_grpc_context):
        """Returns all derived components."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryComponentsCommand(pattern="*")
            response = service.QueryComponents(request, mock_grpc_context)

            assert response.success is True
            assert len(response.components) > 0

    def test_malformed_pin_does_not_fail_the_component_listing(
        self, mock_hal_module, mock_hal_pins, mock_grpc_context
    ):
        """Components are still derived from the other pins and params."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            expected_response = service.QueryComponents(
                hal_pb2.QueryComponentsCommand(pattern="*"), mock_grpc_context
            )
            mock_hal_module.get_info_pins.return_value = [
                {"NAME": None, "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0}
            ] + mock_hal_pins
            response = service.QueryComponents(
                hal_pb2.QueryComponentsCommand(pattern="*"), mock_grpc_context
            )

            assert response.success is True
            assert response.error == ""
            assert len(response.components) > 0
            assert list(response.components) == list(expected_response.components)

    def test_filters_by_pattern(self, mock_hal_module, mock_grpc_context):
        """Filters components by glob pattern."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.QueryComponentsCommand(pattern="axis.*")
            response = service.QueryComponents(request, mock_grpc_context)

            assert response.success is True
            # Should match axis.x component
            component_names = [c.name for c in response.components]
            assert any("axis" in name for name in component_names)

    def test_error_handling(self, mock_hal_module, mock_grpc_context):
        """Returns error on exception."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_pins.side_effect = Exception("HAL error")
            request = hal_pb2.QueryComponentsCommand(pattern="*")
            response = service.QueryComponents(request, mock_grpc_context)

            assert response.success is False
            assert "HAL error" in response.error


class TestStreamInterval:
    """Test the poll interval both streams use."""

    def test_default_interval(self, mock_hal_module):
        """Zero (or less) means 100 ms."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import stream_interval_seconds

            assert stream_interval_seconds(0) == 0.1
            assert stream_interval_seconds(-5) == 0.1

    def test_custom_interval(self, mock_hal_module):
        """A requested interval is used as given."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import stream_interval_seconds

            assert stream_interval_seconds(500) == 0.5

    def test_interval_is_clamped_to_the_maximum(self, mock_hal_module):
        """The largest int32 does not become a 25-day wait."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import (
                MAX_STREAM_INTERVAL_SECONDS,
                stream_interval_seconds,
            )

            assert MAX_STREAM_INTERVAL_SECONDS == 60.0
            assert stream_interval_seconds(60000) == 60.0
            assert stream_interval_seconds(60001) == 60.0
            assert stream_interval_seconds(2147483647) == 60.0


class TestStreamStatus:
    """Test StreamStatus RPC."""

    def test_yields_status_updates(self, mock_hal_module, mock_grpc_context):
        """Yields status updates while active."""
        # Make context active for 2 iterations then inactive
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalStreamStatusRequest(interval_ms=1)

            results = list(service.StreamStatus(request, mock_grpc_context))

            assert len(results) == 2
            assert all(r.timestamp > 0 for r in results)

    def test_cancel_ends_a_stream_with_a_long_interval(self, mock_hal_module, mock_grpc_context):
        """A cancelled client frees the worker at once, whatever interval it asked for."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalStreamStatusRequest(interval_ms=2147483647)
            first_status_received = threading.Event()

            consumer_thread, received_statuses = collect_stream_in_thread(
                service.StreamStatus(request, mock_grpc_context),
                on_item=lambda status: first_status_received.set(),
            )
            assert first_status_received.wait(STREAM_END_TIMEOUT_SECONDS)

            # gRPC runs the registered callbacks when the RPC ends.
            mock_grpc_context.add_callback.assert_called_once()
            rpc_ended_callback = mock_grpc_context.add_callback.call_args[0][0]
            rpc_ended_callback()

            consumer_thread.join(STREAM_END_TIMEOUT_SECONDS)
            assert not consumer_thread.is_alive()
            assert len(received_statuses) == 1

    def test_rpc_that_already_ended_yields_nothing(self, mock_hal_module, mock_grpc_context):
        """add_callback returns False when the RPC is over; the stream then stops."""
        mock_grpc_context.add_callback.return_value = False

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalStreamStatusRequest(interval_ms=2147483647)

            assert list(service.StreamStatus(request, mock_grpc_context)) == []

    def test_repeated_failures_abort_once_with_the_cause(self, mock_hal_module, mock_grpc_context):
        """The status set for repeated failures is not replaced by a second abort."""
        # Real gRPC raises from abort(); the mock has to as well for this.
        mock_grpc_context.abort.side_effect = Exception()

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import (
                HalServiceServicer,
                MAX_CONSECUTIVE_POLL_ERRORS,
            )
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_pins.side_effect = Exception("HAL gone")
            request = hal_pb2.HalStreamStatusRequest(interval_ms=1)

            with pytest.raises(Exception):
                list(service.StreamStatus(request, mock_grpc_context))

            assert mock_hal_module.get_info_pins.call_count == 1 + MAX_CONSECUTIVE_POLL_ERRORS
            mock_grpc_context.abort.assert_called_once_with(
                grpc.StatusCode.INTERNAL, "Too many consecutive errors: HAL gone"
            )

    def test_has_writer_is_not_asked_again_on_every_poll(self, mock_hal_module, mock_grpc_context):
        """Within the refresh period the stream reuses the has_writer answers."""
        mock_grpc_context.is_active.side_effect = [True, True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.HalStreamStatusRequest(interval_ms=1)

            with patch("linuxcnc_grpc.hal_service.time.monotonic", return_value=100.0):
                results = list(service.StreamStatus(request, mock_grpc_context))

            assert len(results) == 3
            assert all(pin.has_writer for status in results for pin in status.pins)
            # Three pins, asked once each, for three status messages.
            assert mock_hal_module.pin_has_writer.call_count == 3


class TestWatchValues:
    """Test WatchValues RPC."""

    def test_rejects_empty_names(self, mock_hal_module, mock_grpc_context):
        """Sets error when names list is empty."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=[], interval_ms=100)

            results = list(service.WatchValues(request, mock_grpc_context))

            assert len(results) == 0
            mock_grpc_context.set_code.assert_called_with(grpc.StatusCode.INVALID_ARGUMENT)

    def test_detects_value_changes(self, mock_hal_module, mock_grpc_context):
        """Yields batch when values change."""
        # First call for init returns 1.0, second loop returns 2.0
        mock_hal_module.get_value.side_effect = [1.0, 2.0]
        # One check while setting up, one poll, then the client is gone.
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(
                names=["axis.x.pos-cmd"],
                interval_ms=1
            )

            results = list(service.WatchValues(request, mock_grpc_context))

            # Should have one batch with one change
            assert len(results) == 1
            assert len(results[0].changes) == 1
            assert results[0].changes[0].old_value.float_value == 1.0
            assert results[0].changes[0].new_value.float_value == 2.0

    def test_no_yield_when_unchanged(self, mock_hal_module, mock_grpc_context):
        """Does not yield when values unchanged."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = [True, True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(
                names=["axis.x.pos-cmd"],
                interval_ms=1
            )

            results = list(service.WatchValues(request, mock_grpc_context))

            # Should have no batches since no changes
            assert len(results) == 0
            # One read while setting up and one for each of the two polls.
            assert mock_hal_module.get_value.call_count == 3

    def test_unchanged_nan_is_not_a_change(self, mock_hal_module, mock_grpc_context):
        """A value that stays NaN does not notify on every poll."""
        mock_hal_module.get_value.side_effect = [math.nan, math.nan, math.nan, 1.5]
        mock_grpc_context.is_active.side_effect = [True, True, True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["axis.x.pos-cmd"], interval_ms=1)

            results = list(service.WatchValues(request, mock_grpc_context))

            # Only the step from NaN to 1.5 is a change.
            assert len(results) == 1
            assert math.isnan(results[0].changes[0].old_value.float_value)
            assert results[0].changes[0].new_value.float_value == 1.5

    def test_duplicate_names_are_watched_once(self, mock_hal_module, mock_grpc_context):
        """A name given several times is read and reported once per poll."""
        mock_hal_module.get_value.side_effect = [1.0, 2.0]
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["axis.x.pos-cmd"] * 50, interval_ms=1)

            results = list(service.WatchValues(request, mock_grpc_context))

            assert mock_hal_module.get_value.call_count == 2
            assert len(results) == 1
            assert len(results[0].changes) == 1

    def test_names_keep_their_order(self, mock_hal_module, mock_grpc_context):
        """De-duplication keeps the order in which names were first given."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = [True, True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["b", "a", "b", "c", "a"], interval_ms=1)

            list(service.WatchValues(request, mock_grpc_context))

            read_names = [call.args[0] for call in mock_hal_module.get_value.call_args_list]
            assert read_names == ["b", "a", "c"]

    def test_rejects_too_many_names(self, mock_hal_module, mock_grpc_context):
        """More distinct names than the limit is INVALID_ARGUMENT, with nothing read."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer, MAX_WATCHED_NAMES
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            names = [f"comp.{index}.pin" for index in range(MAX_WATCHED_NAMES + 1)]
            request = hal_pb2.WatchRequest(names=names, interval_ms=1)

            results = list(service.WatchValues(request, mock_grpc_context))

            assert results == []
            mock_grpc_context.abort.assert_called_once()
            assert mock_grpc_context.abort.call_args[0][0] == grpc.StatusCode.INVALID_ARGUMENT
            mock_hal_module.get_value.assert_not_called()

    def test_accepts_the_largest_allowed_number_of_names(self, mock_hal_module, mock_grpc_context):
        """Exactly the limit is accepted; duplicates do not count towards it."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = None
        mock_grpc_context.is_active.return_value = True
        mock_grpc_context.add_callback.return_value = True

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer, MAX_WATCHED_NAMES
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            names = [f"comp.{index}.pin" for index in range(MAX_WATCHED_NAMES)]
            request = hal_pb2.WatchRequest(names=names + names, interval_ms=2147483647)
            setup_done = threading.Event()
            reads_until_setup_done = MAX_WATCHED_NAMES

            def get_value(name):
                nonlocal reads_until_setup_done
                reads_until_setup_done -= 1
                if reads_until_setup_done <= 0:
                    setup_done.set()
                return 1.0

            mock_hal_module.get_value.side_effect = get_value
            consumer_thread, _ = collect_stream_in_thread(
                service.WatchValues(request, mock_grpc_context)
            )
            assert setup_done.wait(STREAM_END_TIMEOUT_SECONDS)
            mock_grpc_context.add_callback.call_args[0][0]()
            consumer_thread.join(STREAM_END_TIMEOUT_SECONDS)

            assert not consumer_thread.is_alive()
            mock_grpc_context.abort.assert_not_called()

    def test_rejects_unknown_name(self, mock_hal_module, mock_grpc_context):
        """A name that cannot be read at the start is INVALID_ARGUMENT."""
        mock_hal_module.get_value.side_effect = RuntimeError("not found")

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["missing.pin"], interval_ms=1)

            results = list(service.WatchValues(request, mock_grpc_context))

            assert results == []
            mock_grpc_context.abort.assert_called_once()
            assert mock_grpc_context.abort.call_args[0][0] == grpc.StatusCode.INVALID_ARGUMENT
            assert "missing.pin" in mock_grpc_context.abort.call_args[0][1]

    def test_setup_stops_when_the_client_has_gone(self, mock_hal_module, mock_grpc_context):
        """Setup does not go on reading names for a client that left."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            names = [f"comp.{index}.pin" for index in range(100)]
            request = hal_pb2.WatchRequest(names=names, interval_ms=1)

            results = list(service.WatchValues(request, mock_grpc_context))

            assert results == []
            assert mock_hal_module.get_value.call_count == 2

    def test_setup_reads_the_hal_lists_once(self, mock_hal_module, mock_grpc_context):
        """Types for all names come from one read of the lists, not one per name."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = [True] * 20 + [False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            pin_list_reads_before = mock_hal_module.get_info_pins.call_count
            names = [f"comp.{index}.pin" for index in range(20)]
            request = hal_pb2.WatchRequest(names=names, interval_ms=1)

            list(service.WatchValues(request, mock_grpc_context))

            assert mock_hal_module.get_info_pins.call_count == pin_list_reads_before + 1
            assert mock_hal_module.get_info_signals.call_count == 1
            assert mock_hal_module.get_info_params.call_count == 1

    def test_cancel_ends_a_watch_with_a_long_interval(self, mock_hal_module, mock_grpc_context):
        """A cancelled client frees the worker at once, whatever interval it asked for."""
        first_poll_done = threading.Event()
        value_reads = 0

        def get_value(name):
            nonlocal value_reads
            value_reads += 1
            if value_reads == 2:
                # The first read is setup; the second is the first poll.
                first_poll_done.set()
            return 1.0

        mock_hal_module.get_value.side_effect = get_value

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["axis.x.pos-cmd"], interval_ms=2147483647)

            consumer_thread, received_batches = collect_stream_in_thread(
                service.WatchValues(request, mock_grpc_context)
            )
            assert first_poll_done.wait(STREAM_END_TIMEOUT_SECONDS)

            # gRPC runs the registered callbacks when the RPC ends.
            mock_grpc_context.add_callback.assert_called_once()
            rpc_ended_callback = mock_grpc_context.add_callback.call_args[0][0]
            rpc_ended_callback()

            consumer_thread.join(STREAM_END_TIMEOUT_SECONDS)
            assert not consumer_thread.is_alive()
            assert received_batches == []
            assert value_reads == 2

    def test_watch_ends_when_a_name_keeps_failing(self, mock_hal_module, mock_grpc_context):
        """A watched pin that went away ends the stream with a status that names it."""
        # Real gRPC raises from abort(); the mock has to as well for this.
        mock_grpc_context.abort.side_effect = Exception()
        value_reads_by_name = {"axis.x.pos-cmd": 0, "removed.pin": 0}

        def get_value(name):
            value_reads_by_name[name] += 1
            if name == "removed.pin" and value_reads_by_name[name] > 1:
                raise RuntimeError("pin not found")
            return float(value_reads_by_name[name])

        mock_hal_module.get_value.side_effect = get_value

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import (
                HalServiceServicer,
                MAX_CONSECUTIVE_POLL_ERRORS,
            )
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(
                names=["axis.x.pos-cmd", "removed.pin"], interval_ms=1
            )
            received_batches = []

            with pytest.raises(Exception):
                for batch in service.WatchValues(request, mock_grpc_context):
                    received_batches.append(batch)

            mock_grpc_context.abort.assert_called_once_with(
                grpc.StatusCode.INTERNAL,
                "Too many consecutive errors: cannot read 'removed.pin': pin not found",
            )
            # The healthy name was still reported on every poll until the end.
            assert len(received_batches) == MAX_CONSECUTIVE_POLL_ERRORS
            assert all(
                [change.name for change in batch.changes] == ["axis.x.pos-cmd"]
                for batch in received_batches
            )
            assert value_reads_by_name["removed.pin"] == 1 + MAX_CONSECUTIVE_POLL_ERRORS

    def test_a_failure_that_clears_does_not_end_the_watch(self, mock_hal_module, mock_grpc_context):
        """Only failures in a row count; a good poll starts the count again."""
        value_reads = 0

        def get_value(name):
            nonlocal value_reads
            value_reads += 1
            # Setup read, then polls: four failures, one success, four failures.
            if value_reads in (2, 3, 4, 5, 7, 8, 9, 10):
                raise RuntimeError("busy")
            return 1.0

        mock_hal_module.get_value.side_effect = get_value
        mock_grpc_context.is_active.side_effect = [True] * 10 + [False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["axis.x.pos-cmd"], interval_ms=1)

            assert list(service.WatchValues(request, mock_grpc_context)) == []

            mock_grpc_context.abort.assert_not_called()
            assert value_reads == 10

    def test_u32_param_crossing_the_s32_range(self, mock_hal_module, mock_grpc_context):
        """A watched u32 parameter that grows past 2147483647 is reported, as u32."""
        mock_hal_module.get_info_params.return_value = [
            {"NAME": "comp.0.counter", "DIRECTION": 64, "VALUE": 2147483647},
        ]
        mock_hal_module.get_value.side_effect = [2147483647, 2147483648]
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            request = hal_pb2.WatchRequest(names=["comp.0.counter"], interval_ms=1)

            results = list(service.WatchValues(request, mock_grpc_context))

            assert len(results) == 1
            assert results[0].changes[0].old_value.u32_value == 2147483647
            assert results[0].changes[0].new_value.u32_value == 2147483648


class TestMalformedHalItems:
    """One item HAL lists in a shape nobody expects does not fail a query."""

    # Items whose name fnmatch cannot take, and one that is not a dict at all.
    MALFORMED_ITEMS = [
        {"NAME": None, "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0},
        {"NAME": 7, "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0},
        None,
    ]

    @pytest.mark.parametrize(
        "rpc_name, hal_listing, request_type, pattern, response_field, expected_names",
        [
            ("QueryPins", "get_info_pins", "QueryPinsCommand", "axis.*",
             "pins", ["axis.x.pos-cmd"]),
            ("QuerySignals", "get_info_signals", "QuerySignalsCommand", "x-*",
             "signals", ["x-pos-cmd"]),
            ("QueryParams", "get_info_params", "QueryParamsCommand", "pid.*",
             "params", ["pid.x.Pgain"]),
        ],
    )
    def test_filtered_query_leaves_out_an_item_it_cannot_match(
        self, mock_hal_module, mock_grpc_context, caplog,
        rpc_name, hal_listing, request_type, pattern, response_field, expected_names,
    ):
        listing = getattr(mock_hal_module, hal_listing)
        well_formed_items = listing.return_value
        listing.return_value = (
            self.MALFORMED_ITEMS[:1] + well_formed_items + self.MALFORMED_ITEMS[1:]
        )
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_service"):
                response = getattr(service, rpc_name)(
                    getattr(hal_pb2, request_type)(pattern=pattern), mock_grpc_context
                )

        assert response.error == ""
        assert response.success is True
        assert [item.name for item in getattr(response, response_field)] == expected_names
        assert caplog.text.count("whose name cannot be matched") == len(self.MALFORMED_ITEMS)

    def test_pin_writers_skip_a_pin_that_cannot_be_asked(
        self, mock_hal_module, mock_hal_pins, caplog
    ):
        unhashable_name_pin = {"NAME": ["not", "a", "name"]}
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer

            service = HalServiceServicer()
            with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_service"):
                pin_writers = service._read_pin_writers(
                    [None, unhashable_name_pin] + mock_hal_pins
                )

        assert pin_writers == {pin["NAME"]: True for pin in mock_hal_pins}
        assert caplog.text.count("whose name cannot be read") == 2

    def test_pin_that_went_away_is_skipped_without_a_warning(
        self, mock_hal_module, mock_hal_pins, caplog
    ):
        """A pin removed since the listing is normal and not worth a log line."""
        mock_hal_module.pin_has_writer.side_effect = RuntimeError("pin not found")
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer

            service = HalServiceServicer()
            with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_service"):
                pin_writers = service._read_pin_writers(mock_hal_pins)

        assert pin_writers == {}
        assert caplog.text == ""

    def test_filtered_component_query_leaves_out_a_component_it_cannot_match(
        self, mock_hal_module, mock_hal_pins, mock_grpc_context
    ):
        """A pin named by a tuple becomes a component whose name is that tuple."""
        mock_hal_module.get_info_pins.return_value = [
            {"NAME": ("not", "text"), "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0}
        ] + mock_hal_pins
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.QueryComponents(
                hal_pb2.QueryComponentsCommand(pattern="axis.*"), mock_grpc_context
            )

        assert response.error == ""
        assert response.success is True
        assert [component.name for component in response.components] == ["axis.x"]

    def test_unfiltered_pin_query_survives_an_item_that_is_not_a_dict(
        self, mock_hal_module, mock_hal_pins, mock_grpc_context
    ):
        mock_hal_module.get_info_pins.return_value = [None] + mock_hal_pins
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            response = service.QueryPins(
                hal_pb2.QueryPinsCommand(pattern="*"), mock_grpc_context
            )
            status = service.GetSystemStatus(
                hal_pb2.GetSystemStatusRequest(), mock_grpc_context
            )

        assert response.error == ""
        assert response.success is True
        expected_names = [pin["NAME"] for pin in mock_hal_pins]
        assert [pin.name for pin in response.pins] == expected_names
        mock_grpc_context.abort.assert_not_called()
        assert [pin.name for pin in status.pins] == expected_names
