"""
Unit tests for HalServiceServicer.

Tests the gRPC service layer for HAL introspection.
"""

import sys
from unittest.mock import MagicMock, patch

import grpc
import pytest


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

    def test_exception_handling(self, mock_hal_module, mock_grpc_context):
        """Sets error code on exception."""
        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            from linuxcnc_grpc.hal_service import HalServiceServicer
            from linuxcnc_pb import hal_pb2

            service = HalServiceServicer()
            mock_hal_module.get_info_pins.side_effect = Exception("HAL error")
            request = hal_pb2.GetSystemStatusRequest()
            service.GetSystemStatus(request, mock_grpc_context)

            mock_grpc_context.set_code.assert_called_with(grpc.StatusCode.INTERNAL)


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


class TestStreamStatus:
    """Test StreamStatus RPC."""

    def test_yields_status_updates(self, mock_hal_module, mock_grpc_context):
        """Yields status updates while active."""
        # Make context active for 2 iterations then inactive
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            with patch("time.sleep"):
                from linuxcnc_grpc.hal_service import HalServiceServicer
                from linuxcnc_pb import hal_pb2

                service = HalServiceServicer()
                request = hal_pb2.HalStreamStatusRequest(interval_ms=1)

                results = list(service.StreamStatus(request, mock_grpc_context))

                assert len(results) == 2
                assert all(r.timestamp > 0 for r in results)

    def test_uses_default_interval(self, mock_hal_module, mock_grpc_context):
        """Uses default 100ms interval when not specified."""
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            with patch("time.sleep") as mock_sleep:
                from linuxcnc_grpc.hal_service import HalServiceServicer
                from linuxcnc_pb import hal_pb2

                service = HalServiceServicer()
                request = hal_pb2.HalStreamStatusRequest(interval_ms=0)

                list(service.StreamStatus(request, mock_grpc_context))

                mock_sleep.assert_called_with(0.1)

    def test_uses_custom_interval(self, mock_hal_module, mock_grpc_context):
        """Uses custom interval when specified."""
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            with patch("time.sleep") as mock_sleep:
                from linuxcnc_grpc.hal_service import HalServiceServicer
                from linuxcnc_pb import hal_pb2

                service = HalServiceServicer()
                request = hal_pb2.HalStreamStatusRequest(interval_ms=500)

                list(service.StreamStatus(request, mock_grpc_context))

                mock_sleep.assert_called_with(0.5)


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
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            with patch("time.sleep"):
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

    def test_no_yield_when_unchanged(self, mock_hal_module, mock_grpc_context):
        """Does not yield when values unchanged."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            with patch("time.sleep"):
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

    def test_uses_default_interval(self, mock_hal_module, mock_grpc_context):
        """Uses default 0.1s interval when not specified."""
        mock_hal_module.get_value.return_value = 1.0
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.dict(sys.modules, {"hal": mock_hal_module}):
            with patch("time.sleep") as mock_sleep:
                from linuxcnc_grpc.hal_service import HalServiceServicer
                from linuxcnc_pb import hal_pb2

                service = HalServiceServicer()
                request = hal_pb2.WatchRequest(
                    names=["axis.x.pos-cmd"],
                    interval_ms=0
                )

                list(service.WatchValues(request, mock_grpc_context))

                mock_sleep.assert_called_with(0.1)
