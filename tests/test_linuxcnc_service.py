"""
Unit tests for LinuxCNCServiceServicer.

Tests the gRPC service layer for LinuxCNC machine control.
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, call, patch

import grpc
import pytest

from linuxcnc_grpc.error_broker import ErrorBroker


@pytest.fixture(autouse=True)
def stop_error_readers_after_each_test():
    """
    Stop the error reader thread of every servicer a test constructs.

    The tests here build servicers inline and do not close them; a reader
    thread left running would be counted by later tests that check threads.
    """
    started_brokers = []
    real_start = ErrorBroker.start

    def start_and_remember(broker):
        started_brokers.append(broker)
        real_start(broker)

    with patch.object(ErrorBroker, "start", start_and_remember):
        yield
    for broker in started_brokers:
        broker.stop()


class TestLinuxCNCServiceInit:
    """Test LinuxCNCServiceServicer initialization."""

    def test_init_success(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Service initializes with LinuxCNC connection."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

            service = LinuxCNCServiceServicer()
            assert service._stat is not None
            mock_linuxcnc_stat.poll.assert_called_once()

    def test_init_stat_failure(self, mock_linuxcnc_module):
        """Service propagates exception when stat() fails."""
        mock_linuxcnc_module.stat.side_effect = Exception("LinuxCNC not running")

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

            with pytest.raises(Exception, match="LinuxCNC not running"):
                LinuxCNCServiceServicer()


class TestGetStatus:
    """Test GetStatus RPC."""

    def test_success(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        """Returns complete LinuxCNC status."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            # Force reimport to use updated mock
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.GetStatusRequest()
            response = service.GetStatus(request, mock_grpc_context)

            assert response.timestamp > 0
            assert response.task.task_mode == linuxcnc_pb2.MODE_MANUAL
            assert len(response.joints) == 3

    def test_linuxcnc_error(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        """Sets error code on linuxcnc.error after reconnect fails."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            # Force reimport to use updated mock
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            with patch("linuxcnc_grpc.linuxcnc_service.time.sleep"):
                from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
                from linuxcnc_pb import linuxcnc_pb2

                service = LinuxCNCServiceServicer()
                mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error(
                    "Connection lost"
                )
                request = linuxcnc_pb2.GetStatusRequest()
                service.GetStatus(request, mock_grpc_context)

                # After reconnect fails, aborts with UNAVAILABLE
                mock_grpc_context.abort.assert_called_once()
                args = mock_grpc_context.abort.call_args
                assert args[0][0] == grpc.StatusCode.UNAVAILABLE


class TestSendCommand:
    """Test SendCommand RPC dispatch."""

    def test_unknown_command_type(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for unknown command type."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            # Empty command - WhichOneof returns None
            request = linuxcnc_pb2.LinuxCNCCommand(serial=1)
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "Unknown command type" in response.error_message

    def test_uses_provided_serial(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Uses serial from request."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                serial=999, state=linuxcnc_pb2.StateCommand(state=linuxcnc_pb2.STATE_ON)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.serial == 999

    def test_auto_increments_serial(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Auto-increments serial when not provided."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request1 = linuxcnc_pb2.LinuxCNCCommand(
                state=linuxcnc_pb2.StateCommand(state=linuxcnc_pb2.STATE_ON)
            )
            request2 = linuxcnc_pb2.LinuxCNCCommand(
                state=linuxcnc_pb2.StateCommand(state=linuxcnc_pb2.STATE_ON)
            )
            response1 = service.SendCommand(request1, mock_grpc_context)
            response2 = service.SendCommand(request2, mock_grpc_context)

            assert response2.serial == response1.serial + 1


class TestStateCommand:
    """Test state command handler."""

    @pytest.mark.parametrize(
        "state,expected",
        [
            ("STATE_ESTOP", "STATE_ESTOP"),
            ("STATE_ESTOP_RESET", "STATE_ESTOP_RESET"),
            ("STATE_ON", "STATE_ON"),
            ("STATE_OFF", "STATE_OFF"),
        ],
    )
    def test_state_commands(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, state, expected
    ):
        """Tests all state transitions."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            state_enum = getattr(linuxcnc_pb2, state)
            request = linuxcnc_pb2.LinuxCNCCommand(
                state=linuxcnc_pb2.StateCommand(state=state_enum)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.state.assert_called_once()


class TestModeCommand:
    """Test mode command handler."""

    @pytest.mark.parametrize("mode", ["MODE_MANUAL", "MODE_AUTO", "MODE_MDI"])
    def test_mode_commands(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, mode
    ):
        """Tests all mode transitions."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            mode_enum = getattr(linuxcnc_pb2, mode)
            request = linuxcnc_pb2.LinuxCNCCommand(
                mode=linuxcnc_pb2.ModeCommand(mode=mode_enum)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.mode.assert_called_once()


class TestMdiCommand:
    """Test MDI command handler."""

    def test_sends_gcode(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Sends G-code to MDI."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command="G0 X10 Y20")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.mdi.assert_called_once_with("G0 X10 Y20")

    def test_mdi_with_coordinates(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Sends MDI command with full coordinate specification."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command="G1 X10 Y20 Z5 F100")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.mdi.assert_called_once_with("G1 X10 Y20 Z5 F100")


class TestMdiValidation:
    """Test MDI command input validation."""

    def test_rejects_empty_command(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for empty MDI command."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command="")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "empty" in response.error_message.lower()

    def test_rejects_whitespace_only_command(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for whitespace-only MDI command."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command="   ")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "empty" in response.error_message.lower()

    def test_rejects_null_bytes(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for MDI command containing null bytes."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command="G0 X10\x00")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "null" in response.error_message.lower()


class TestIndexValidation:
    """Test joint/axis/spindle index validation."""

    def test_rejects_invalid_joint_index(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for out-of-bounds joint index."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.JOG_STOP = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_STOP, is_joint=True, index=999
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "out of range" in response.error_message.lower()

    def test_rejects_axis_index_minus_one(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for axis index -1 (only valid for joints)."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.JOG_STOP = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_STOP, is_joint=False, index=-1
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "not valid" in response.error_message.lower()

    def test_rejects_invalid_spindle_index(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for out-of-bounds spindle index."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.SPINDLE_FORWARD = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                spindle=linuxcnc_pb2.SpindleControlCommand(
                    command=linuxcnc_pb2.SPINDLE_CMD_FORWARD, speed=1000.0, spindle=99
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "out of range" in response.error_message.lower()


class TestProgramPathValidation:
    """Test program open path validation."""

    def test_rejects_path_traversal(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for paths containing '..'."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}), \
             patch.dict(os.environ, {"LINUXCNC_NC_FILES": "/home/linuxcnc/nc_files"}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(open="../../etc/passwd")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "allowed directory" in response.error_message

    def test_rejects_empty_path(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for empty program path."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(open="  ")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "empty" in response.error_message.lower()

    def test_rejects_null_bytes_in_path(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns error for paths containing null bytes."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(open="/path/to/file\x00.ngc")
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR
            assert "null" in response.error_message.lower()


class TestJogCommand:
    """Test jog command handler."""

    def test_jog_stop(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        """Stops jogging."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.JOG_STOP = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_STOP, is_joint=True, index=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_jog_continuous(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Continuous jog with velocity."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.JOG_CONTINUOUS = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_CONTINUOUS,
                    is_joint=False,
                    index=0,
                    velocity=100.0,
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_jog_increment(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Incremental jog."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.JOG_INCREMENT = 2

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_INCREMENT,
                    is_joint=True,
                    index=1,
                    velocity=50.0,
                    increment=0.1,
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE


class TestHomeCommand:
    """Test home command handler."""

    def test_home_joint(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Homes specific joint."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                home=linuxcnc_pb2.HomeCommand(joint=0)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.home.assert_called_once_with(0)

    def test_home_all(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        """Homes all joints with -1."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                home=linuxcnc_pb2.HomeCommand(joint=-1)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.home.assert_called_once_with(-1)


class TestUnhomeCommand:
    """Test unhome command handler."""

    def test_unhome_joint(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Unhomes a specific joint."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                unhome=linuxcnc_pb2.UnhomeCommand(joint=0)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.unhome.assert_called_once_with(0)


class TestSpindleCommand:
    """Test spindle command handler."""

    def test_spindle_forward(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Starts spindle forward."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.SPINDLE_FORWARD = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                spindle=linuxcnc_pb2.SpindleControlCommand(
                    command=linuxcnc_pb2.SPINDLE_CMD_FORWARD, speed=1000.0, spindle=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_spindle_reverse(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Starts spindle in reverse."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.SPINDLE_REVERSE = -1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                spindle=linuxcnc_pb2.SpindleControlCommand(
                    command=linuxcnc_pb2.SPINDLE_CMD_REVERSE, speed=500.0, spindle=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_spindle_off(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Stops spindle."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.SPINDLE_OFF = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                spindle=linuxcnc_pb2.SpindleControlCommand(
                    command=linuxcnc_pb2.SPINDLE_CMD_OFF, spindle=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_spindle_increase(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Increases spindle speed."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.SPINDLE_INCREASE = 2

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                spindle=linuxcnc_pb2.SpindleControlCommand(
                    command=linuxcnc_pb2.SPINDLE_CMD_INCREASE, spindle=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE


class TestBrakeCommand:
    """Test brake command handler."""

    def test_brake_engage(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Engages spindle brake."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.BRAKE_ENGAGE = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                brake=linuxcnc_pb2.BrakeCommand(
                    state=linuxcnc_pb2.BRAKE_ENGAGE, spindle=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE


class TestFeedrateCommand:
    """Test feedrate command handler."""

    def test_set_feedrate(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Sets feedrate override."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                feedrate=linuxcnc_pb2.FeedrateCommand(scale=0.5)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.feedrate.assert_called_once_with(0.5)

    def test_set_rapidrate(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Sets rapid override."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                rapidrate=linuxcnc_pb2.RapidrateCommand(scale=0.25)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.rapidrate.assert_called_once_with(0.25)


class TestSpindleOverrideCommand:
    """Test spindle override command handler."""

    def test_set_spindle_override(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Sets spindle override."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                spindle_override=linuxcnc_pb2.SpindleOverrideCommand(
                    scale=1.2, spindle=0
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE


class TestCoolantCommand:
    """Test coolant command handler."""

    def test_mist_on(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        """Turns on mist."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.MIST_ON = 1
        mock_linuxcnc_module.MIST_OFF = 0
        mock_linuxcnc_module.FLOOD_OFF = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                coolant=linuxcnc_pb2.CoolantCommand(mist=True, flood=False)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_flood_on(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Turns on flood."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.MIST_OFF = 0
        mock_linuxcnc_module.FLOOD_ON = 1
        mock_linuxcnc_module.FLOOD_OFF = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                coolant=linuxcnc_pb2.CoolantCommand(mist=False, flood=True)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_mist_and_flood_on(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Turns on both mist and flood."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.MIST_ON = 1
        mock_linuxcnc_module.FLOOD_ON = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                coolant=linuxcnc_pb2.CoolantCommand(mist=True, flood=True)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.mist.assert_called_once()
            mock_cmd.flood.assert_called_once()


class TestProgramCommand:
    """Test program command handler."""

    def test_open_program(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Opens a program file."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            # Use a temp directory as the allowed NC files directory
            with tempfile.TemporaryDirectory() as tmpdir:
                # Resolve symlinks (macOS /var -> /private/var)
                tmpdir = str(os.path.realpath(tmpdir))
                test_file = os.path.join(tmpdir, "file.ngc")
                open(test_file, 'w').close()  # Create the file
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.LinuxCNCCommand(
                        program=linuxcnc_pb2.ProgramCommand(open=test_file)
                    )
                    response = service.SendCommand(request, mock_grpc_context)

                    assert response.status == linuxcnc_pb2.RCS_DONE
                    mock_cmd.program_open.assert_called_once_with(test_file)

    def test_run_program(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Runs program from line."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.AUTO_RUN = 0

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(run_from_line=0)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_pause_program(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Pauses running program."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.AUTO_PAUSE = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(pause=True)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_resume_program(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Resumes paused program."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.AUTO_RESUME = 2

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(resume=True)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_step_program(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Steps through program."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.AUTO_STEP = 3

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(step=True)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_abort_program(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Aborts running program."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(abort=True)
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.abort.assert_called_once()


class FakeLinuxCNCCommand:
    """
    Stand-in for linuxcnc.command() that tracks a real integer ``serial``.

    Like the real bindings, each sent command bumps ``serial`` and the task
    echoes it back in ``stat.echo_serial_number`` before the send returns.
    """

    def __init__(self, stat, first_serial=100, exposes_serial=True):
        self._stat = stat
        self._next_linuxcnc_serial = first_serial
        self.sent_commands = []
        # Set to an exception to make flood() fail, as the second message of
        # a coolant command.
        self.flood_error = None
        # Names of the calls whose write LinuxCNC refuses. The real bindings
        # then return normally and leave ``serial`` and the echo untouched.
        self.calls_whose_write_fails = set()
        if exposes_serial:
            self.serial = 0

    def _send(self, name, *args):
        if name in self.calls_whose_write_fails:
            return
        linuxcnc_serial = self._next_linuxcnc_serial
        self._next_linuxcnc_serial += 1
        if hasattr(self, "serial"):
            self.serial = linuxcnc_serial
        self._stat.echo_serial_number = linuxcnc_serial
        self.sent_commands.append((name, args))

    def state(self, *args):
        self._send("state", *args)

    def mist(self, *args):
        self._send("mist", *args)

    def flood(self, *args):
        if self.flood_error is not None:
            raise self.flood_error
        self._send("flood", *args)

    def mdi(self, *args):
        self._send("mdi", *args)


class LockThatReportsFailedAttempts:
    """
    Wraps the servicer lock and sets an event when an acquire() times out.

    A test that waits for the event knows another thread really was blocked
    on the lock, without sleeping and hoping.
    """

    def __init__(self, wrapped_lock):
        import threading

        self._wrapped_lock = wrapped_lock
        self.an_attempt_failed = threading.Event()

    def acquire(self, *args, **kwargs):
        acquired = self._wrapped_lock.acquire(*args, **kwargs)
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


INT32_MAX = 2**31 - 1
INT32_MIN = -(2**31)


class TestWaitComplete:
    """Test WaitComplete RPC."""

    @staticmethod
    def _make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command):
        """Build a servicer wired to the given stat and command stand-ins."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = command
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        # Import the protobuf modules before patch.dict: anything first
        # imported inside it is dropped from sys.modules on exit, and a second
        # import of the generated code would then clash with the first.
        from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            # Force reimport so the service module binds to this test's mock
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

            return LinuxCNCServiceServicer()

    @staticmethod
    def _estop_request(serial=0):
        from linuxcnc_pb import linuxcnc_pb2

        request = linuxcnc_pb2.LinuxCNCCommand(serial=serial)
        request.state.state = linuxcnc_pb2.STATE_ESTOP
        return request

    @staticmethod
    def _mdi_request(gcode, serial=0):
        from linuxcnc_pb import linuxcnc_pb2

        request = linuxcnc_pb2.LinuxCNCCommand(serial=serial)
        request.mdi.command = gcode
        return request

    def test_rcs_done(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns RCS_DONE when the awaited command has completed."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        request = linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=5.0)
        response = service.WaitComplete(request, mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.error_message == ""
        assert response.serial == send_response.serial

    def test_rcs_error(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns RCS_ERROR when the awaited command failed."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_ERROR

        request = linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=5.0)
        response = service.WaitComplete(request, mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "Command failed"

    @pytest.mark.parametrize("task_state_name", ["RCS_DONE", "RCS_EXEC", "RCS_ERROR"])
    def test_nothing_sent_reports_done_immediately(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, task_state_name
    ):
        """With no command of ours to wait for, the answer is RCS_DONE at once."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.state = getattr(mock_linuxcnc_module, task_state_name)
        service = self._make_service(
            mock_linuxcnc_module, mock_linuxcnc_stat, MagicMock()
        )

        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        with patch.object(time, "sleep") as sleep_between_polls:
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(timeout=30.0), mock_grpc_context
            )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.error_message == ""
        # At once: decided on the first poll, without ever sleeping.
        sleep_between_polls.assert_not_called()
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait + 1

    def test_linuxcnc_error_reported_as_rcs_error(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A linuxcnc.error while polling is returned as RCS_ERROR with its text."""
        from linuxcnc_pb import linuxcnc_pb2

        service = self._make_service(
            mock_linuxcnc_module, mock_linuxcnc_stat, MagicMock()
        )
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("NML gone")

        request = linuxcnc_pb2.WaitCompleteRequest(serial=1, timeout=5.0)
        response = service.WaitComplete(request, mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "NML gone"

    def test_default_timeout(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Uses 5.0s default timeout when not specified."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        # Fake clock: every sleep advances monotonic time by the slept amount.
        fake_clock = {"now": 1000.0}
        slept_durations = []

        def fake_sleep(duration):
            slept_durations.append(duration)
            fake_clock["now"] += duration

        with patch.object(
            time, "monotonic", side_effect=lambda: fake_clock["now"]
        ), patch.object(time, "sleep", side_effect=fake_sleep):
            request = linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0)
            response = service.WaitComplete(request, mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_EXEC
        assert sum(slept_durations) == pytest.approx(5.0)
        assert max(slept_durations) <= 0.05 + 1e-9

    def test_timeout_returns_rcs_exec(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A command still executing at the deadline reports RCS_EXEC, no error text."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        # Fake clock: every sleep advances monotonic time by the slept amount,
        # so the length of the wait does not depend on how busy the machine is.
        fake_clock = {"now": 1000.0}
        slept_durations = []

        def fake_sleep(duration):
            slept_durations.append(duration)
            fake_clock["now"] += duration

        with patch.object(
            time, "monotonic", side_effect=lambda: fake_clock["now"]
        ), patch.object(time, "sleep", side_effect=fake_sleep):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.2),
                mock_grpc_context,
            )

        assert response.status == linuxcnc_pb2.RCS_EXEC
        assert response.error_message == ""
        assert response.serial == send_response.serial
        assert sum(slept_durations) == pytest.approx(0.2)

    def test_stops_waiting_when_client_disconnects(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A cancelled RPC stops polling instead of running out the timeout."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        mock_grpc_context.is_active.return_value = False

        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        with patch.object(time, "sleep") as sleep_between_polls:
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=30.0),
                mock_grpc_context,
            )

        assert response.status == linuxcnc_pb2.RCS_EXEC
        assert response.error_message == ""
        # It polled once, saw the client gone and left without sleeping.
        sleep_between_polls.assert_not_called()
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait + 1

    @staticmethod
    def _hold_service_lock(service):
        """
        Take the servicer lock on a helper thread, as another RPC would.

        Returns once the lock is held, with the event that releases it and
        the holding thread.
        """
        import threading

        lock_is_held = threading.Event()
        release_lock = threading.Event()

        def hold_lock():
            with service._lock:
                lock_is_held.set()
                release_lock.wait(timeout=30)

        lock_holder = threading.Thread(target=hold_lock, daemon=True)
        lock_holder.start()
        assert lock_is_held.wait(timeout=5)
        return release_lock, lock_holder

    def test_times_out_while_another_rpc_holds_the_lock_at_the_start(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A wait that cannot get the lock before its deadline reports a timeout."""
        import threading
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE
        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        wait_responses = []

        def run_wait():
            wait_responses.append(service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.2),
                mock_grpc_context,
            ))

        release_lock, lock_holder = self._hold_service_lock(service)
        try:
            wait_thread = threading.Thread(target=run_wait, daemon=True)
            wait_thread.start()
            # The lock is held until the finally below, so the wait can only
            # return by giving up on it.
            wait_thread.join(timeout=10)
            wait_returned_while_lock_was_held = not wait_thread.is_alive()
        finally:
            release_lock.set()
            lock_holder.join(timeout=5)

        assert wait_returned_while_lock_was_held, "WaitComplete overran its timeout"
        assert wait_responses[0].status == linuxcnc_pb2.RCS_EXEC
        assert wait_responses[0].error_message == ""
        assert wait_responses[0].serial == send_response.serial
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait
        mock_grpc_context.abort.assert_not_called()

    def test_times_out_while_another_rpc_holds_the_lock_when_taking_the_record(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """The acquisition that resolves the dispatch record is bounded too."""
        import threading
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE
        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        real_lock = service._lock
        lock_holds = []
        wait_responses = []
        hold_service_lock = self._hold_service_lock

        class LockTakenElsewhereBeforeSecondTake:
            """Passes everything to the real lock; another thread takes it
            just before WaitComplete takes it the second time (the first
            time is the connection check)."""

            def __init__(self):
                self.takes_by_the_wait = 0

            def _before_take(self):
                if threading.current_thread() is not wait_thread:
                    return
                self.takes_by_the_wait += 1
                if self.takes_by_the_wait == 2:
                    lock_holds.append(hold_service_lock(service))

            def acquire(self, *args, **kwargs):
                self._before_take()
                return real_lock.acquire(*args, **kwargs)

            def release(self):
                real_lock.release()

            def __enter__(self):
                self._before_take()
                return real_lock.__enter__()

            def __exit__(self, *exception_details):
                return real_lock.__exit__(*exception_details)

        def run_wait():
            wait_responses.append(service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.2),
                mock_grpc_context,
            ))

        wait_thread = threading.Thread(target=run_wait, daemon=True)
        service._lock = LockTakenElsewhereBeforeSecondTake()
        try:
            wait_thread.start()
            wait_thread.join(timeout=10)
            wait_returned_while_lock_was_held = not wait_thread.is_alive()
        finally:
            for release_lock, lock_holder in lock_holds:
                release_lock.set()
                lock_holder.join(timeout=5)
            service._lock = real_lock

        assert len(lock_holds) == 1
        assert wait_returned_while_lock_was_held, "WaitComplete overran its timeout"
        assert wait_responses[0].status == linuxcnc_pb2.RCS_EXEC
        assert wait_responses[0].error_message == ""
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait

    def test_times_out_while_another_rpc_holds_the_lock_between_polls(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """The deadline also bounds the lock acquisition of each later poll."""
        import threading
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        lock_holds = []
        wait_responses = []

        def sleep_during_which_the_lock_is_taken(duration):
            # Runs on the WaitComplete thread where it sleeps between polls,
            # with the lock released; from here on another RPC keeps it.
            if not lock_holds:
                lock_holds.append(self._hold_service_lock(service))

        def run_wait():
            wait_responses.append(service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=1.0),
                mock_grpc_context,
            ))

        try:
            with patch.object(time, "sleep", side_effect=sleep_during_which_the_lock_is_taken):
                wait_thread = threading.Thread(target=run_wait, daemon=True)
                wait_thread.start()
                wait_thread.join(timeout=10)
                wait_returned_while_lock_was_held = not wait_thread.is_alive()
        finally:
            for release_lock, lock_holder in lock_holds:
                release_lock.set()
                lock_holder.join(timeout=5)

        assert len(lock_holds) == 1
        assert wait_returned_while_lock_was_held, "WaitComplete overran its timeout"
        assert wait_responses[0].status == linuxcnc_pb2.RCS_EXEC
        assert wait_responses[0].error_message == ""
        # Only the first poll happened; the second never got the lock.
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait + 1

    def test_free_lock_is_still_taken_with_a_tiny_timeout(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """An uncontended wait polls once and reports the result however short the timeout."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=1e-9),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE

    def test_send_command_not_blocked_during_wait(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """An estop sent while WaitComplete sleeps between polls is not blocked."""
        import threading
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        # A long move is dispatched and stays in RCS_EXEC.
        move_response = service.SendCommand(self._mdi_request("G1 X100 F1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        estop_responses = []
        estop_finished_during_sleep = []

        def run_estop():
            estop_responses.append(
                service.SendCommand(self._estop_request(), mock_grpc_context)
            )

        def sleep_that_sends_estop(duration):
            # Runs on the WaitComplete thread, exactly where the service
            # sleeps between polls. If the service still held its lock here,
            # the estop on the helper thread could not get through.
            if estop_finished_during_sleep:
                return
            estop_thread = threading.Thread(target=run_estop, daemon=True)
            estop_thread.start()
            # A blocked estop would never finish while this sleep lasts, so
            # the bound only has to be longer than a busy machine needs.
            estop_thread.join(timeout=10)
            estop_finished_during_sleep.append(not estop_thread.is_alive())

        with patch.object(time, "sleep", side_effect=sleep_that_sends_estop):
            wait_response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=move_response.serial, timeout=2.0),
                mock_grpc_context,
            )

        assert estop_finished_during_sleep == [True], "estop was blocked behind WaitComplete"
        assert estop_responses[0].status == linuxcnc_pb2.RCS_DONE
        assert ("state", (mock_linuxcnc_module.STATE_ESTOP,)) in command.sent_commands
        # The estop was echoed after the move, so the move is reported done.
        assert wait_response.status == linuxcnc_pb2.RCS_DONE

    def test_done_immediately_once_echo_serial_has_passed(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Echo serial past ours means done, even while a later command executes."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        # Someone else's later command (501) is now executing.
        mock_linuxcnc_stat.echo_serial_number = 501
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        with patch.object(time, "sleep") as service_sleep:
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=30.0),
                mock_grpc_context,
            )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.error_message == ""
        service_sleep.assert_not_called()

    def test_send_command_returns_linuxcnc_serial(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """SendCommand reports LinuxCNC's own serial next to the server's."""
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=700)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        first_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        second_response = service.SendCommand(
            self._mdi_request("G0 X2", serial=42), mock_grpc_context
        )

        assert first_response.HasField("linuxcnc_serial")
        assert first_response.linuxcnc_serial == 700
        assert second_response.linuxcnc_serial == 701
        # The server-side serial is unchanged by the new field.
        assert first_response.serial == 1
        assert second_response.serial == 42

    def test_send_command_leaves_linuxcnc_serial_unset_without_bindings_support(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """No linuxcnc_serial when the bindings don't expose command.serial."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, exposes_serial=False)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not response.HasField("linuxcnc_serial")

    def test_send_command_leaves_linuxcnc_serial_unset_when_rejected(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A command the server refused was never dispatched, so it has no serial."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        response = service.SendCommand(self._mdi_request("   "), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert not response.HasField("linuxcnc_serial")

    def test_requested_linuxcnc_serial_is_waited_on_instead_of_the_latest(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """With linuxcnc_serial set, an earlier command is reported, not the latest."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        first_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        service.SendCommand(self._mdi_request("G0 X2"), mock_grpc_context)
        # The second command is still executing; the first is long finished.
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=first_response.linuxcnc_serial, timeout=0.2
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE

    def test_requested_linuxcnc_serial_reports_that_commands_error(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """With linuxcnc_serial set to the command the task failed on, RCS_ERROR."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_ERROR

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=send_response.linuxcnc_serial, timeout=0.2
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "Command failed"

    def test_requested_linuxcnc_serial_still_executing_times_out(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A wait on the command the task is still executing returns RCS_EXEC at the timeout."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        with patch.object(time, "sleep"):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(
                    linuxcnc_serial=send_response.linuxcnc_serial, timeout=0.05
                ),
                mock_grpc_context,
            )

        assert response.status == linuxcnc_pb2.RCS_EXEC

    @pytest.mark.parametrize("serial_offset", [5, -50])
    def test_linuxcnc_serial_this_server_never_issued_is_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, serial_offset
    ):
        """A serial that SendCommand did not return gets RCS_ERROR, not a guess."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE
        unknown_serial = send_response.linuxcnc_serial + serial_offset

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=4, linuxcnc_serial=unknown_serial, timeout=0.2),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "was not issued by this server" in response.error_message
        assert str(unknown_serial) in response.error_message
        assert response.serial == 4

    def test_explicit_zero_linuxcnc_serial_is_not_treated_as_unset(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """linuxcnc_serial=0 is a named target (and an unknown one), not "the latest command"."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(linuxcnc_serial=0, timeout=0.2),
            mock_grpc_context,
        )

        # An untargeted wait would answer RCS_DONE here.
        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "was not issued by this server" in response.error_message

    def test_linuxcnc_serial_from_before_a_reconnect_is_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A serial issued on the previous connection is not compared with the new one."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        # The server reconnects (LinuxCNC restarted) before the wait starts.
        # The new instance has already counted past 500 for other commands.
        service._connect()
        mock_linuxcnc_stat.echo_serial_number = 600
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=send_response.linuxcnc_serial, timeout=0.2
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "was not issued by this server" in response.error_message

    def test_reconnect_during_wait_on_requested_serial_reports_unknown_outcome(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A wait on a named serial ends with RCS_ERROR if the server reconnects."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        sleep_calls = []

        def sleep_that_reconnects(duration):
            sleep_calls.append(duration)
            if len(sleep_calls) == 1:
                service._connect()
                # A restarted LinuxCNC counts from the start again; without
                # the generation check the wait would see 600 > 500 as "done".
                mock_linuxcnc_stat.echo_serial_number = 600
                mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        with patch.object(time, "sleep", side_effect=sleep_that_reconnects):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(
                    serial=9, linuxcnc_serial=send_response.linuxcnc_serial, timeout=2.0
                ),
                mock_grpc_context,
            )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "Reconnected to LinuxCNC" in response.error_message
        assert "500" in response.error_message
        assert response.serial == 9

    @staticmethod
    def _coolant_request():
        from linuxcnc_pb import linuxcnc_pb2

        request = linuxcnc_pb2.LinuxCNCCommand()
        request.coolant.mist = True
        request.coolant.flood = True
        return request

    def test_partly_dispatched_command_becomes_the_one_waited_on(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """If a handler fails after sending one message, WaitComplete waits on that message."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        # Coolant sends mist, then flood. Flood fails, but mist has gone out
        # and is still executing.
        command.flood_error = mock_linuxcnc_module.error("flood failed")
        send_response = service.SendCommand(self._coolant_request(), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        assert send_response.status == linuxcnc_pb2.RCS_ERROR
        assert [name for name, _ in command.sent_commands] == ["mdi", "mist"]
        wait_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(timeout=0.1), mock_grpc_context
        )
        # Without the record for mist, the wait would see the finished MDI
        # command overtaken and answer RCS_DONE.
        assert wait_response.status == linuxcnc_pb2.RCS_EXEC

    def test_command_rejected_before_any_message_keeps_the_previous_record(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A command that sent nothing does not replace what WaitComplete waits on."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        record_before_rejection = service._latest_dispatch_record

        rejected_response = service.SendCommand(self._mdi_request("   "), mock_grpc_context)

        assert rejected_response.status == linuxcnc_pb2.RCS_ERROR
        assert service._latest_dispatch_record == record_before_rejection

    def test_command_linuxcnc_did_not_take_is_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A send that returns normally with command.serial unchanged is RCS_ERROR."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=700)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        first_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        record_before_failed_write = service._latest_dispatch_record
        issued_before_failed_write = list(service._issued_linuxcnc_serials)

        command.calls_whose_write_fails = {"mdi"}
        refused_response = service.SendCommand(
            self._mdi_request("G0 X2", serial=9), mock_grpc_context
        )

        assert refused_response.status == linuxcnc_pb2.RCS_ERROR
        assert "not accepted by LinuxCNC" in refused_response.error_message
        assert refused_response.serial == 9
        # The previous command's serial (still in command.serial) is not
        # handed out as this command's.
        assert command.serial == first_response.linuxcnc_serial
        assert not refused_response.HasField("linuxcnc_serial")
        assert service._latest_dispatch_record == record_before_failed_write
        assert list(service._issued_linuxcnc_serials) == issued_before_failed_write

    def test_wait_after_a_command_linuxcnc_did_not_take_follows_the_previous_one(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """The refused command leaves the legacy wait on the command before it."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        command.calls_whose_write_fails = {"mdi"}
        service.SendCommand(self._mdi_request("G0 X2"), mock_grpc_context)

        # The first move is still executing.
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        executing_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(timeout=0.1), mock_grpc_context
        )
        # The task then fails it.
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_ERROR
        failed_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(timeout=0.2), mock_grpc_context
        )

        assert executing_response.status == linuxcnc_pb2.RCS_EXEC
        assert failed_response.status == linuxcnc_pb2.RCS_ERROR

    def test_first_command_linuxcnc_did_not_take_is_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A new command channel's serial is 0 and stays 0 when the first write fails."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        command.calls_whose_write_fails = {"mdi"}
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert not response.HasField("linuxcnc_serial")
        assert service._latest_dispatch_record is None
        assert list(service._issued_linuxcnc_serials) == []

    def test_multi_message_command_with_no_message_taken_is_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Coolant whose mist and flood writes both fail sent nothing: RCS_ERROR."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        record_before_failed_writes = service._latest_dispatch_record

        command.calls_whose_write_fails = {"mist", "flood"}
        response = service.SendCommand(self._coolant_request(), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert not response.HasField("linuxcnc_serial")
        assert service._latest_dispatch_record == record_before_failed_writes

    @pytest.mark.parametrize("call_whose_write_fails", ["mist", "flood"])
    def test_multi_message_command_with_one_message_taken_is_not_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, call_whose_write_fails
    ):
        """
        One coolant message LinuxCNC did not take goes unnoticed if the other was taken.

        Only the serial before and after the whole handler is compared, so
        the answer is RCS_DONE with the serial of the message that was taken.
        """
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=300)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        command.calls_whose_write_fails = {call_whose_write_fails}
        response = service.SendCommand(self._coolant_request(), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.linuxcnc_serial == 301
        assert list(service._issued_linuxcnc_serials) == [300, 301]

    def test_fallback_cannot_tell_a_command_linuxcnc_did_not_take(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Without command.serial there is nothing to compare, so the answer stays RCS_DONE."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, exposes_serial=False)
        command.calls_whose_write_fails = {"mdi"}
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert not response.HasField("linuxcnc_serial")

    def test_fallback_refuses_to_dispatch_when_the_baseline_cannot_be_sampled(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Without command.serial, a failed status poll stops the command going out untracked."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, exposes_serial=False)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("status unavailable")

        response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "status unavailable" in response.error_message
        assert command.sent_commands == []

    def test_reconnect_during_wait_drops_the_stale_serial(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """After a reconnect mid-wait, the old LinuxCNC serial is not compared against."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        sleep_calls = []

        def sleep_that_reconnects(duration):
            sleep_calls.append(duration)
            if len(sleep_calls) == 1:
                service._connect()
                # The new channel's serials start far below the stale 500,
                # which the old record would wait on until the timeout.
                mock_linuxcnc_stat.echo_serial_number = 3

        with patch.object(time, "sleep", side_effect=sleep_that_reconnects):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=2.0),
                mock_grpc_context,
            )

        # Nothing has been sent on the new channel, so "latest" is empty: done.
        assert response.status == linuxcnc_pb2.RCS_DONE
        assert len(sleep_calls) == 1

    def test_reconnect_during_wait_follows_latest_command_on_new_channel(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """After a reconnect mid-wait, the wait moves to the newest command sent since."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        sleep_calls = []

        def sleep_that_reconnects_then_finishes(duration):
            sleep_calls.append(duration)
            if len(sleep_calls) == 1:
                service._connect()
                command._next_linuxcnc_serial = 7
                service.SendCommand(self._mdi_request("G0 X2"), mock_grpc_context)
                mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
            elif len(sleep_calls) == 3:
                mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_ERROR

        with patch.object(time, "sleep", side_effect=sleep_that_reconnects_then_finishes):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=2.0),
                mock_grpc_context,
            )

        # It kept waiting on the new command (7) and reported that one's result.
        assert len(sleep_calls) == 3
        assert response.status == linuxcnc_pb2.RCS_ERROR

    def test_tracks_linuxcnc_serial_of_latest_dispatch(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """SendCommand remembers LinuxCNC's own serial for the latest command only."""
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        first_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        assert first_response.serial != 500  # server counter, not LinuxCNC's serial
        assert service._latest_dispatch_record.linuxcnc_serial == 500

        second_response = service.SendCommand(
            self._mdi_request("G0 X2", serial=77), mock_grpc_context
        )
        assert second_response.serial == 77
        assert service._latest_dispatch_record.linuxcnc_serial == 501

    @pytest.mark.parametrize("which_serial", ["first", "second", "zero", "unknown"])
    def test_request_serial_is_ignored_and_latest_command_is_reported(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, which_serial
    ):
        """After send A then send B, any WaitComplete reports on B, as it always has."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        first_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        second_response = service.SendCommand(self._mdi_request("G0 X2"), mock_grpc_context)
        requested_serial = {
            "first": first_response.serial,
            "second": second_response.serial,
            "zero": 0,
            "unknown": 424242,
        }[which_serial]

        # LinuxCNC has echoed the second command (501) and it failed.
        mock_linuxcnc_stat.echo_serial_number = 501
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_ERROR

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=requested_serial, timeout=1.0),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "Command failed"
        assert response.serial == requested_serial  # echoed back, nothing more

    def test_request_serial_is_ignored_while_latest_command_executes(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Asking about an older, finished command still waits on the latest one."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        first_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        service.SendCommand(self._mdi_request("G0 X2"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC  # 501 still running

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=first_response.serial, timeout=0.15),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_EXEC

    def test_waits_until_echo_reaches_linuxcnc_serial(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """An idle state is not 'done' until the task has echoed our serial."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)

        # Task is idle but still showing an older serial.
        mock_linuxcnc_stat.echo_serial_number = 499
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        stale_wait = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert stale_wait.status == linuxcnc_pb2.RCS_EXEC

        mock_linuxcnc_stat.echo_serial_number = 500
        caught_up_wait = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert caught_up_wait.status == linuxcnc_pb2.RCS_DONE

    def test_failed_dispatch_does_not_replace_latest_record(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A command rejected by validation is not what WaitComplete waits on."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        record_before_rejection = service._latest_dispatch_record

        response = service.SendCommand(self._mdi_request("", serial=9), mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert service._latest_dispatch_record is record_before_rejection
        assert record_before_rejection.linuxcnc_serial == 500

    def test_fallback_without_command_serial(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Without command.serial, wait for the echo serial to pass its pre-send value."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(
            mock_linuxcnc_stat, first_serial=1235, exposes_serial=False
        )
        mock_linuxcnc_stat.echo_serial_number = 1234
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)

        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        record = service._latest_dispatch_record
        assert record.linuxcnc_serial is None
        assert record.echo_serial_before_dispatch == 1234

        # Echo serial back at its pre-send value: not our command yet.
        mock_linuxcnc_stat.echo_serial_number = 1234
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE
        not_yet_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert not_yet_response.status == linuxcnc_pb2.RCS_EXEC

        # Echo serial advanced but still executing: keep waiting.
        mock_linuxcnc_stat.echo_serial_number = 1235
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        executing_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert executing_response.status == linuxcnc_pb2.RCS_EXEC

        # Advanced and idle: done.
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE
        done_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert done_response.status == linuxcnc_pb2.RCS_DONE

        # Advanced and errored: reported as before.
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_ERROR
        error_response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert error_response.status == linuxcnc_pb2.RCS_ERROR
        assert error_response.error_message == "Command failed"

    def test_reconnect_clears_latest_record(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """The serial from the old command channel is dropped on reconnect."""
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        assert service._latest_dispatch_record is not None
        generation_before_reconnect = service._connection_generation

        service._connect()

        assert service._latest_dispatch_record is None
        assert service._connection_generation == generation_before_reconnect + 1

    @pytest.mark.parametrize("failing_step", ["stat", "command", "error_channel", "poll"])
    def test_failed_reconnect_changes_nothing(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, failing_step
    ):
        """A reconnect attempt that fails leaves channels, record and generation alone."""
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=500)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        original_error_channel = service._error_channel
        record_before_attempt = service._latest_dispatch_record
        generation_before_attempt = service._connection_generation

        # The next connection attempt hands out fresh objects and then fails.
        replacement_stat = MagicMock()
        replacement_stat.echo_serial_number = 3
        replacement_stat.state = mock_linuxcnc_module.RCS_DONE
        mock_linuxcnc_module.stat.return_value = replacement_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        connection_failure = mock_linuxcnc_module.error("LinuxCNC went away")
        if failing_step == "poll":
            replacement_stat.poll.side_effect = connection_failure
        else:
            getattr(mock_linuxcnc_module, failing_step).side_effect = connection_failure

        with pytest.raises(mock_linuxcnc_module.error):
            service._connect()

        assert service._stat is mock_linuxcnc_stat
        assert service._command is command
        assert service._error_channel is original_error_channel
        assert service._latest_dispatch_record is record_before_attempt
        assert service._connection_generation == generation_before_attempt

        # A wait is still judged against the live channel and the real serial:
        # the move (500) is executing, so this must not come back as done.
        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=0.15),
            mock_grpc_context,
        )
        assert response.status == linuxcnc_pb2.RCS_EXEC

    @pytest.mark.parametrize("wait_timeout", [float("inf"), 3600.0])
    def test_cancelled_wait_returns_while_another_rpc_holds_the_lock_at_the_start(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, wait_timeout
    ):
        """A client that cancels gets its worker back without the lock being released."""
        import threading
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        observed_lock = LockThatReportsFailedAttempts(service._lock)
        service._lock = observed_lock
        client_cancelled = threading.Event()
        mock_grpc_context.is_active.side_effect = lambda: not client_cancelled.is_set()
        wait_responses = []

        def run_wait():
            wait_responses.append(service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(
                    serial=send_response.serial, timeout=wait_timeout
                ),
                mock_grpc_context,
            ))

        release_lock, lock_holder = self._hold_service_lock(service)
        try:
            wait_thread = threading.Thread(target=run_wait, daemon=True)
            wait_thread.start()
            # Cancel only once the wait is known to be stuck behind the lock.
            wait_was_blocked_on_the_lock = observed_lock.an_attempt_failed.wait(10)
            client_cancelled.set()
            # The lock stays held until the finally below, so the wait can
            # only return by noticing the cancellation.
            wait_thread.join(timeout=10)
            wait_returned_while_lock_was_held = not wait_thread.is_alive()
        finally:
            release_lock.set()
            lock_holder.join(timeout=5)
            wait_thread.join(timeout=5)

        assert wait_was_blocked_on_the_lock
        assert wait_returned_while_lock_was_held, "a cancelled wait kept its worker"
        assert wait_responses[0].status == linuxcnc_pb2.RCS_EXEC
        assert wait_responses[0].error_message == ""
        assert wait_responses[0].serial == send_response.serial
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait
        mock_grpc_context.abort.assert_not_called()

    def test_cancelled_wait_returns_while_another_rpc_holds_the_lock_between_polls(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """The same holds for the lock acquisition of each later poll."""
        import threading
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G1 X5 F5"), mock_grpc_context)
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        polls_before_wait = mock_linuxcnc_stat.poll.call_count
        observed_lock = LockThatReportsFailedAttempts(service._lock)
        service._lock = observed_lock
        client_cancelled = threading.Event()
        mock_grpc_context.is_active.side_effect = lambda: not client_cancelled.is_set()
        lock_holds = []
        wait_responses = []

        def sleep_during_which_the_lock_is_taken(duration):
            # Runs on the WaitComplete thread where it sleeps between polls,
            # with the lock released; from here on another RPC keeps it.
            if not lock_holds:
                lock_holds.append(self._hold_service_lock(service))

        def run_wait():
            wait_responses.append(service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(
                    serial=send_response.serial, timeout=float("inf")
                ),
                mock_grpc_context,
            ))

        try:
            with patch.object(time, "sleep", side_effect=sleep_during_which_the_lock_is_taken):
                wait_thread = threading.Thread(target=run_wait, daemon=True)
                wait_thread.start()
                wait_was_blocked_on_the_lock = observed_lock.an_attempt_failed.wait(10)
                client_cancelled.set()
                wait_thread.join(timeout=10)
                wait_returned_while_lock_was_held = not wait_thread.is_alive()
        finally:
            for release_lock, lock_holder in lock_holds:
                release_lock.set()
                lock_holder.join(timeout=5)
            wait_thread.join(timeout=5)

        assert len(lock_holds) == 1
        assert wait_was_blocked_on_the_lock
        assert wait_returned_while_lock_was_held, "a cancelled wait kept its worker"
        assert wait_responses[0].status == linuxcnc_pb2.RCS_EXEC
        assert wait_responses[0].error_message == ""
        # Only the first poll happened; the second never got the lock.
        assert mock_linuxcnc_stat.poll.call_count == polls_before_wait + 1

    # LinuxCNC's serial is a C int. Around the wrap from INT32_MAX to
    # INT32_MIN the newer serial is the smaller number, and the comparison
    # has to come out as LinuxCNC's own signed subtraction does.
    @pytest.mark.parametrize(
        "awaited_serial, echo_serial, task_state_name, expected_state_name",
        [
            # Echo behind the awaited serial: keep waiting, whatever the state.
            (INT32_MIN, INT32_MAX, "RCS_DONE", None),
            (INT32_MIN, INT32_MAX, "RCS_ERROR", None),
            (INT32_MIN + 3, INT32_MAX - 1, "RCS_DONE", None),
            (INT32_MAX, INT32_MAX - 1, "RCS_DONE", None),
            (INT32_MIN + 1, INT32_MIN, "RCS_DONE", None),
            # Echo equal to it: the task's state decides.
            (INT32_MAX, INT32_MAX, "RCS_DONE", "RCS_DONE"),
            (INT32_MAX, INT32_MAX, "RCS_EXEC", None),
            (INT32_MIN, INT32_MIN, "RCS_ERROR", "RCS_ERROR"),
            (INT32_MIN, INT32_MIN, "RCS_EXEC", None),
            # Echo past it: done, whatever the state.
            (INT32_MAX, INT32_MIN, "RCS_EXEC", "RCS_DONE"),
            (INT32_MAX, INT32_MIN, "RCS_ERROR", "RCS_DONE"),
            (INT32_MAX - 1, INT32_MIN + 3, "RCS_EXEC", "RCS_DONE"),
            (INT32_MAX - 1, INT32_MAX, "RCS_EXEC", "RCS_DONE"),
            (INT32_MIN, INT32_MIN + 1, "RCS_EXEC", "RCS_DONE"),
        ],
    )
    def test_completion_state_across_the_serial_rollover(
        self,
        mock_linuxcnc_module,
        mock_linuxcnc_stat,
        mock_grpc_context,
        awaited_serial,
        echo_serial,
        task_state_name,
        expected_state_name,
    ):
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        dispatch_record = service._latest_dispatch_record._replace(
            linuxcnc_serial=awaited_serial
        )

        completion_state = service._completion_state(
            dispatch_record, echo_serial, getattr(mock_linuxcnc_module, task_state_name)
        )

        expected_state = (
            None
            if expected_state_name is None
            else getattr(mock_linuxcnc_module, expected_state_name)
        )
        assert completion_state == expected_state

    @pytest.mark.parametrize(
        "echo_serial_before_dispatch, echo_serial, task_state_name, expected_state_name",
        [
            # Echo not past the sample taken before dispatch: keep waiting.
            (INT32_MIN, INT32_MAX, "RCS_DONE", None),
            (INT32_MIN + 3, INT32_MAX - 1, "RCS_ERROR", None),
            (INT32_MAX, INT32_MAX, "RCS_DONE", None),
            (INT32_MIN, INT32_MIN, "RCS_DONE", None),
            # Echo past the sample: the task's state decides.
            (INT32_MAX, INT32_MIN, "RCS_DONE", "RCS_DONE"),
            (INT32_MAX, INT32_MIN, "RCS_ERROR", "RCS_ERROR"),
            (INT32_MAX, INT32_MIN, "RCS_EXEC", None),
            (INT32_MAX - 1, INT32_MIN + 3, "RCS_DONE", "RCS_DONE"),
            (INT32_MAX - 1, INT32_MAX, "RCS_DONE", "RCS_DONE"),
        ],
    )
    def test_fallback_completion_state_across_the_serial_rollover(
        self,
        mock_linuxcnc_module,
        mock_linuxcnc_stat,
        mock_grpc_context,
        echo_serial_before_dispatch,
        echo_serial,
        task_state_name,
        expected_state_name,
    ):
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, exposes_serial=False)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        assert service._latest_dispatch_record.linuxcnc_serial is None
        dispatch_record = service._latest_dispatch_record._replace(
            echo_serial_before_dispatch=echo_serial_before_dispatch
        )

        completion_state = service._completion_state(
            dispatch_record, echo_serial, getattr(mock_linuxcnc_module, task_state_name)
        )

        expected_state = (
            None
            if expected_state_name is None
            else getattr(mock_linuxcnc_module, expected_state_name)
        )
        assert completion_state == expected_state

    def test_wait_reports_done_when_the_echo_serial_wrapped_past_the_command(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """The command got LinuxCNC's largest serial; a later one wrapped to the smallest."""
        import time
        from linuxcnc_pb import linuxcnc_pb2

        command = FakeLinuxCNCCommand(mock_linuxcnc_stat, first_serial=INT32_MAX)
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat, command)
        send_response = service.SendCommand(self._mdi_request("G0 X1"), mock_grpc_context)
        assert service._latest_dispatch_record.linuxcnc_serial == INT32_MAX
        # Another client's command, still executing.
        mock_linuxcnc_stat.echo_serial_number = INT32_MIN
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        with patch.object(time, "sleep") as sleep_between_polls:
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(serial=send_response.serial, timeout=30.0),
                mock_grpc_context,
            )

        assert response.status == linuxcnc_pb2.RCS_DONE
        sleep_between_polls.assert_not_called()


class TestResponseSerialCounter:
    """The server's own serial counter must fit CommandResponse.serial (int32)."""

    def test_counter_wraps_to_one_after_the_largest_int32(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        largest_int32 = 2**31 - 1
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = TestWaitComplete._make_service(
            mock_linuxcnc_module, mock_linuxcnc_stat, command
        )
        service._command_serial = largest_int32 - 1

        response_serials = [
            service.SendCommand(
                TestWaitComplete._mdi_request("G0 X1"), mock_grpc_context
            ).serial
            for _ in range(3)
        ]

        assert response_serials == [largest_int32, 1, 2]
        assert len(command.sent_commands) == 3
        assert linuxcnc_pb2.CommandResponse(serial=service._next_serial()).serial == 3


def record_stream_waits(mock_grpc_context):
    """
    Record how long a StreamStatus call waits between updates, without waiting.

    The stream hands its wake-up event's set() to context.add_callback. This
    takes the event from there and replaces its wait() with one that records
    the timeout and reports "not woken". Returns the list of timeouts.
    """
    waited_seconds = []

    def capture_wake_event(wake_callback):
        wake_event = wake_callback.__self__

        def record_wait(timeout=None):
            waited_seconds.append(timeout)
            return False

        wake_event.wait = record_wait
        return True

    mock_grpc_context.add_callback.side_effect = capture_wake_event
    return waited_seconds


class TestStreamStatus:
    """Test StreamStatus RPC."""

    def test_yields_status_updates(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Yields status while context active."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            # Force reimport to use updated mock
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.StreamStatusRequest(interval_ms=1)

            results = list(service.StreamStatus(request, mock_grpc_context))

            assert len(results) == 2
            assert all(r.timestamp > 0 for r in results)

    def test_uses_default_interval(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Uses default 100ms interval when not specified."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            # Force reimport to use updated mock
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            waited_seconds = record_stream_waits(mock_grpc_context)
            request = linuxcnc_pb2.StreamStatusRequest(interval_ms=0)

            list(service.StreamStatus(request, mock_grpc_context))

            assert waited_seconds == [0.1]


class TestOverrideLimitsCommand:
    """Test override limits command handler."""

    def test_override_limits(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Overrides soft limits."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                override_limits=linuxcnc_pb2.OverrideLimitsCommand()
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            mock_cmd.override_limits.assert_called_once()


class TestProgramOptionsCommand:
    """Test program options command handler."""

    def test_set_optional_stop_on(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Enables optional stop."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program_options=linuxcnc_pb2.ProgramOptionsCommand(
                    optional_stop=True, block_delete=False
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE

    def test_set_block_delete_on(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Enables block delete."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.LinuxCNCCommand(
                program_options=linuxcnc_pb2.ProgramOptionsCommand(
                    optional_stop=False, block_delete=True
                )
            )
            response = service.SendCommand(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE


class TestUploadFile:
    """Test UploadFile RPC."""

    def test_upload_new_file(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Uploads a new file and verifies content on disk."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="test.ngc",
                        content="G0 X10 Y10\n"
                    )
                    response = service.UploadFile(request, mock_grpc_context)

                    assert response.path == os.path.join(tmpdir, "test.ngc")
                    assert response.overwritten is False
                    with open(os.path.join(tmpdir, "test.ngc")) as f:
                        assert f.read() == "G0 X10 Y10\n"

    def test_upload_overwrites_by_default(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Overwrites existing file when fail_if_exists is not set."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                # Create existing file
                existing = os.path.join(tmpdir, "test.ngc")
                with open(existing, 'w') as f:
                    f.write("old content")

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="test.ngc",
                        content="new content"
                    )
                    response = service.UploadFile(request, mock_grpc_context)

                    assert response.overwritten is True
                    with open(existing) as f:
                        assert f.read() == "new content"

    def test_upload_fail_if_exists(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Rejects upload when file exists and fail_if_exists is set."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                existing = os.path.join(tmpdir, "test.ngc")
                with open(existing, 'w') as f:
                    f.write("existing")

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="test.ngc",
                        content="new content",
                        fail_if_exists=True
                    )
                    service.UploadFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.ALREADY_EXISTS

    def test_upload_creates_subdirectory(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Creates subdirectories as needed."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="subdir/nested/test.ngc",
                        content="G0 X0\n"
                    )
                    response = service.UploadFile(request, mock_grpc_context)

                    expected = os.path.join(tmpdir, "subdir", "nested", "test.ngc")
                    assert response.path == expected
                    assert os.path.exists(expected)

    def test_upload_rejects_path_traversal(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Rejects path traversal attempts."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="../../etc/evil.ngc",
                        content="bad content"
                    )
                    service.UploadFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.INVALID_ARGUMENT

    def test_upload_rejects_empty_content(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Rejects empty content."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="test.ngc",
                        content=""
                    )
                    service.UploadFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.INVALID_ARGUMENT

    def test_upload_rejects_empty_filename(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Rejects empty filename."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.UploadFileRequest(
                        filename="",
                        content="G0 X0\n"
                    )
                    service.UploadFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.INVALID_ARGUMENT


class TestListFiles:
    """Test ListFiles RPC."""

    def test_list_root_directory(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Lists files in the root nc_files directory."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                # Create test files
                with open(os.path.join(tmpdir, "file1.ngc"), 'w') as f:
                    f.write("G0 X0\n")
                with open(os.path.join(tmpdir, "file2.ngc"), 'w') as f:
                    f.write("G0 X10\n")
                os.mkdir(os.path.join(tmpdir, "subdir"))

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.ListFilesRequest()
                    response = service.ListFiles(request, mock_grpc_context)

                    assert response.directory == tmpdir
                    assert len(response.files) == 3
                    names = [f.name for f in response.files]
                    assert "file1.ngc" in names
                    assert "file2.ngc" in names
                    assert "subdir" in names

    def test_list_subdirectory(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Lists files in a subdirectory."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                subdir = os.path.join(tmpdir, "subdir")
                os.mkdir(subdir)
                with open(os.path.join(subdir, "nested.ngc"), 'w') as f:
                    f.write("G0 X0\n")

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.ListFilesRequest(subdirectory="subdir")
                    response = service.ListFiles(request, mock_grpc_context)

                    assert len(response.files) == 1
                    assert response.files[0].name == "nested.ngc"
                    assert response.files[0].path == "subdir/nested.ngc"

    def test_list_nonexistent_directory(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns NOT_FOUND for nonexistent directory."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.ListFilesRequest(subdirectory="nonexistent")
                    service.ListFiles(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.NOT_FOUND

    def test_list_returns_file_info(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Verifies FileInfo fields are populated correctly."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                test_content = "G0 X10 Y20\nG1 Z-5 F100\n"
                with open(os.path.join(tmpdir, "part.ngc"), 'w') as f:
                    f.write(test_content)
                os.mkdir(os.path.join(tmpdir, "projects"))

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.ListFilesRequest()
                    response = service.ListFiles(request, mock_grpc_context)

                    # Find the file entry
                    file_entry = next(f for f in response.files if f.name == "part.ngc")
                    assert file_entry.size_bytes == len(test_content)
                    assert file_entry.modified_timestamp > 0
                    assert file_entry.is_directory is False

                    # Find the directory entry
                    dir_entry = next(f for f in response.files if f.name == "projects")
                    assert dir_entry.is_directory is True


class TestDeleteFile:
    """Test DeleteFile RPC."""

    def test_delete_existing_file(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Deletes an existing file."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                target = os.path.join(tmpdir, "delete_me.ngc")
                with open(target, 'w') as f:
                    f.write("G0 X0\n")

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.DeleteFileRequest(filename="delete_me.ngc")
                    response = service.DeleteFile(request, mock_grpc_context)

                    assert response.path == target
                    assert not os.path.exists(target)

    def test_delete_nonexistent_file(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns NOT_FOUND for nonexistent file."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.DeleteFileRequest(filename="nonexistent.ngc")
                    service.DeleteFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.NOT_FOUND

    def test_delete_rejects_path_traversal(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Rejects path traversal attempts."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.DeleteFileRequest(
                        filename="../../etc/passwd"
                    )
                    service.DeleteFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.INVALID_ARGUMENT

    def test_delete_rejects_directory(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Rejects attempts to delete directories."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = str(os.path.realpath(tmpdir))
                os.mkdir(os.path.join(tmpdir, "subdir"))

                with patch.dict(os.environ, {"LINUXCNC_NC_FILES": tmpdir}):
                    service = LinuxCNCServiceServicer()
                    request = linuxcnc_pb2.DeleteFileRequest(filename="subdir")
                    service.DeleteFile(request, mock_grpc_context)

                    mock_grpc_context.abort.assert_called_once()
                    args = mock_grpc_context.abort.call_args
                    assert args[0][0] == grpc.StatusCode.INVALID_ARGUMENT


def make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, command):
    """
    Build a servicer bound to this test's linuxcnc stand-in and command object.

    The stand-in gets the numeric constants the real module has, each
    distinct within its group, so a test can assert the exact arguments a
    handler passed to the command object.
    """
    mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
    mock_linuxcnc_module.command.return_value = command
    mock_linuxcnc_module.error_channel.return_value = MagicMock()
    mock_linuxcnc_module.SPINDLE_FORWARD = 1
    mock_linuxcnc_module.SPINDLE_REVERSE = -1
    mock_linuxcnc_module.SPINDLE_OFF = 0
    mock_linuxcnc_module.SPINDLE_INCREASE = 10
    mock_linuxcnc_module.SPINDLE_DECREASE = 11
    mock_linuxcnc_module.SPINDLE_CONSTANT = 12
    mock_linuxcnc_module.BRAKE_RELEASE = 0
    mock_linuxcnc_module.BRAKE_ENGAGE = 1
    mock_linuxcnc_module.JOG_STOP = 0
    mock_linuxcnc_module.JOG_CONTINUOUS = 1
    mock_linuxcnc_module.JOG_INCREMENT = 2

    # Import the protobuf modules before patch.dict: anything first imported
    # inside it is dropped from sys.modules on exit.
    from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

    with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
        sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
        from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

        return LinuxCNCServiceServicer()


class TestSpindleCommandArguments:
    """The spindle handler addresses the requested spindle, with the right arguments."""

    @staticmethod
    def _send_spindle_command(
        mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, **spindle_fields
    ):
        from linuxcnc_pb import linuxcnc_pb2

        # Two spindles, so a command for spindle 1 is valid and
        # distinguishable from one that lands on spindle 0.
        mock_linuxcnc_stat.spindle = [dict(mock_linuxcnc_stat.spindle[0]) for _ in range(2)]
        mock_linuxcnc_stat.spindles = 2
        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)
        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                spindle=linuxcnc_pb2.SpindleControlCommand(**spindle_fields)
            ),
            mock_grpc_context,
        )
        return response, mock_cmd

    @pytest.mark.parametrize(
        "command_name, linuxcnc_constant",
        [
            ("SPINDLE_CMD_OFF", 0),
            ("SPINDLE_CMD_INCREASE", 10),
            ("SPINDLE_CMD_DECREASE", 11),
            ("SPINDLE_CMD_CONSTANT", 12),
        ],
    )
    def test_operations_without_a_speed_pass_the_spindle_as_second_argument(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        command_name, linuxcnc_constant,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=getattr(linuxcnc_pb2, command_name), spindle=1,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.spindle(linuxcnc_constant, 1)]

    @pytest.mark.parametrize(
        "command_name, linuxcnc_constant",
        [("SPINDLE_CMD_FORWARD", 1), ("SPINDLE_CMD_REVERSE", -1)],
    )
    def test_forward_and_reverse_pass_speed_then_spindle(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        command_name, linuxcnc_constant,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=getattr(linuxcnc_pb2, command_name), speed=1200.0, spindle=1,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.spindle(linuxcnc_constant, 1200.0, 1)]

    def test_wait_for_at_speed_is_passed_as_fourth_argument_when_set(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=linuxcnc_pb2.SPINDLE_CMD_FORWARD, speed=800.0, spindle=1,
            wait_for_at_speed=True,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.spindle(1, 800.0, 1, True)]

    @pytest.mark.parametrize("command_name", ["SPINDLE_CMD_FORWARD", "SPINDLE_CMD_REVERSE"])
    @pytest.mark.parametrize(
        "bad_speed", [-1.0, -1200.0, float("nan"), float("inf"), float("-inf")]
    )
    def test_negative_or_non_finite_speed_is_rejected_without_dispatch(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        command_name, bad_speed,
    ):
        """A negative speed would turn the spindle the other way."""
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=getattr(linuxcnc_pb2, command_name), speed=bad_speed, spindle=0,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "spindle speed" in response.error_message
        assert mock_cmd.method_calls == []

    def test_zero_speed_is_accepted(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=linuxcnc_pb2.SPINDLE_CMD_FORWARD, speed=0.0, spindle=0,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.spindle(1, 0.0, 0)]

    @pytest.mark.parametrize("unused_speed", [-500.0, float("nan"), float("inf")])
    def test_spindle_off_is_never_refused_over_the_speed_field(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, unused_speed
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=linuxcnc_pb2.SPINDLE_CMD_OFF, speed=unused_speed, spindle=1,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.spindle(0, 1)]

    def test_unknown_spindle_command_is_rejected_without_dispatch(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_spindle_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            command=99, spindle=0,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "Unknown spindle command" in response.error_message
        assert mock_cmd.method_calls == []


class TestBrakeCommandArguments:
    """Only the two known brake states act; nothing else releases the brake."""

    @staticmethod
    def _send_brake_command(
        mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, brake_state
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.spindle = [dict(mock_linuxcnc_stat.spindle[0]) for _ in range(2)]
        mock_linuxcnc_stat.spindles = 2
        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)
        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                brake=linuxcnc_pb2.BrakeCommand(state=brake_state, spindle=1)
            ),
            mock_grpc_context,
        )
        return response, mock_cmd

    def test_engage(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_brake_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            linuxcnc_pb2.BRAKE_ENGAGE,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.brake(1, 1)]

    def test_release(self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_brake_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            linuxcnc_pb2.BRAKE_RELEASE,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.brake(0, 1)]

    @pytest.mark.parametrize("unknown_brake_state", [2, 99])
    def test_unknown_state_is_rejected_and_does_not_release_the_brake(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        unknown_brake_state,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, mock_cmd = self._send_brake_command(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            unknown_brake_state,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "Unknown brake state" in response.error_message
        assert mock_cmd.method_calls == []


NOT_FINITE = [float("nan"), float("inf"), float("-inf")]


def _command_builders_forwarding_a_float():
    """One request builder per float field a handler forwards to LinuxCNC."""
    from linuxcnc_pb import linuxcnc_pb2

    def tool_offset_with(**changed_field):
        fields = dict(
            tool_number=1, z_offset=1.0, x_offset=0.5, diameter=6.0,
            front_angle=0.0, back_angle=0.0, orientation=0,
        )
        fields.update(changed_field)
        return linuxcnc_pb2.LinuxCNCCommand(tool_offset=linuxcnc_pb2.ToolOffsetCommand(**fields))

    return {
        "jog continuous velocity": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(type=linuxcnc_pb2.JOG_CONTINUOUS, index=0, velocity=value)
        ),
        "jog increment velocity": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(type=linuxcnc_pb2.JOG_INCREMENT, index=0, velocity=value, increment=1.0)
        ),
        "jog increment distance": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(type=linuxcnc_pb2.JOG_INCREMENT, index=0, velocity=10.0, increment=value)
        ),
        "spindle override scale": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            spindle_override=linuxcnc_pb2.SpindleOverrideCommand(scale=value, spindle=0)
        ),
        "feedrate scale": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            feedrate=linuxcnc_pb2.FeedrateCommand(scale=value)
        ),
        "rapidrate scale": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            rapidrate=linuxcnc_pb2.RapidrateCommand(scale=value)
        ),
        "maximum velocity": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            maxvel=linuxcnc_pb2.MaxVelCommand(velocity=value)
        ),
        "tool z_offset": lambda value: tool_offset_with(z_offset=value),
        "tool x_offset": lambda value: tool_offset_with(x_offset=value),
        "tool diameter": lambda value: tool_offset_with(diameter=value),
        "tool front_angle": lambda value: tool_offset_with(front_angle=value),
        "tool back_angle": lambda value: tool_offset_with(back_angle=value),
        "analog output value": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            analog_output=linuxcnc_pb2.AnalogOutputCommand(index=0, value=value)
        ),
        "set_limit min_limit": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            set_limit=linuxcnc_pb2.SetLimitCommand(joint=0, min_limit=value, max_limit=10.0)
        ),
        "set_limit max_limit": lambda value: linuxcnc_pb2.LinuxCNCCommand(
            set_limit=linuxcnc_pb2.SetLimitCommand(joint=0, min_limit=-10.0, max_limit=value)
        ),
    }


FLOAT_FIELD_NAMES = [
    "jog continuous velocity", "jog increment velocity", "jog increment distance",
    "spindle override scale", "feedrate scale", "rapidrate scale", "maximum velocity",
    "tool z_offset", "tool x_offset", "tool diameter", "tool front_angle",
    "tool back_angle", "analog output value", "set_limit min_limit",
    "set_limit max_limit",
]
MUST_NOT_BE_NEGATIVE_FIELD_NAMES = [
    "spindle override scale", "feedrate scale", "rapidrate scale", "maximum velocity",
]


class TestNumberValidation:
    """Numbers LinuxCNC cannot sensibly act on are refused before anything is sent."""

    def test_every_float_field_has_a_builder(self):
        assert sorted(_command_builders_forwarding_a_float()) == sorted(FLOAT_FIELD_NAMES)

    @pytest.mark.parametrize("field_name", FLOAT_FIELD_NAMES)
    @pytest.mark.parametrize("not_finite_value", NOT_FINITE)
    def test_non_finite_value_is_rejected_without_dispatch(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        field_name, not_finite_value,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)
        request = _command_builders_forwarding_a_float()[field_name](not_finite_value)

        response = service.SendCommand(request, mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "finite" in response.error_message
        assert not response.HasField("linuxcnc_serial")
        assert mock_cmd.method_calls == []

    @pytest.mark.parametrize("field_name", MUST_NOT_BE_NEGATIVE_FIELD_NAMES)
    def test_negative_scale_or_velocity_limit_is_rejected_without_dispatch(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, field_name
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)
        request = _command_builders_forwarding_a_float()[field_name](-0.5)

        response = service.SendCommand(request, mock_grpc_context)

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "must not be negative" in response.error_message
        assert mock_cmd.method_calls == []

    def test_zero_scales_and_velocity_limit_are_accepted(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Zero is a real setting (feed override 0 holds the feed)."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)
        builders = _command_builders_forwarding_a_float()

        for field_name in MUST_NOT_BE_NEGATIVE_FIELD_NAMES:
            response = service.SendCommand(builders[field_name](0.0), mock_grpc_context)
            assert response.status == linuxcnc_pb2.RCS_DONE, field_name

        assert mock_cmd.method_calls == [
            call.spindleoverride(0.0, 0),
            call.feedrate(0.0),
            call.rapidrate(0.0),
            call.maxvel(0.0),
        ]

    def test_negative_jog_velocity_is_a_direction_and_is_accepted(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)

        continuous_response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_CONTINUOUS, is_joint=True, index=2, velocity=-15.0
                )
            ),
            mock_grpc_context,
        )
        increment_response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_INCREMENT, is_joint=False, index=1,
                    velocity=-15.0, increment=-0.25,
                )
            ),
            mock_grpc_context,
        )

        assert continuous_response.status == linuxcnc_pb2.RCS_DONE
        assert increment_response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [
            call.jog(1, True, 2, -15.0),
            call.jog(2, False, 1, -15.0, -0.25),
        ]

    @pytest.mark.parametrize("unused_value", NOT_FINITE)
    def test_jog_stop_is_never_refused_over_fields_it_does_not_use(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, unused_value
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_STOP, is_joint=True, index=1,
                    velocity=unused_value, increment=unused_value,
                )
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.jog(0, True, 1)]

    def test_continuous_jog_is_not_refused_over_the_unused_increment(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(
                    type=linuxcnc_pb2.JOG_CONTINUOUS, index=0, velocity=5.0,
                    increment=float("nan"),
                )
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [call.jog(1, False, 0, 5.0)]

    def test_set_limit_sends_min_then_max(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                set_limit=linuxcnc_pb2.SetLimitCommand(joint=1, min_limit=-10.0, max_limit=10.0)
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert mock_cmd.method_calls == [
            call.set_min_limit(1, -10.0),
            call.set_max_limit(1, 10.0),
        ]

    @pytest.mark.parametrize(
        "min_limit, max_limit", [(10.0, -10.0), (5.0, 5.0), (0.0, 0.0)]
    )
    def test_set_limit_needs_min_below_max_and_sends_neither_message_otherwise(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        min_limit, max_limit,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                set_limit=linuxcnc_pb2.SetLimitCommand(
                    joint=0, min_limit=min_limit, max_limit=max_limit
                )
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert "must be less than max_limit" in response.error_message
        assert mock_cmd.method_calls == []

    def test_set_limit_with_a_bad_max_does_not_send_the_min_first(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Both limits are checked before the first of the two messages."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)

        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                set_limit=linuxcnc_pb2.SetLimitCommand(
                    joint=0, min_limit=-10.0, max_limit=float("inf")
                )
            ),
            mock_grpc_context,
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        mock_cmd.set_min_limit.assert_not_called()
        mock_cmd.set_max_limit.assert_not_called()


# LinuxCNC's status always carries this many joint entries, whatever the
# machine has.
STAT_JOINT_ENTRIES = 16


def give_stat_a_full_joint_array(mock_linuxcnc_stat, configured_joint_count):
    """Shape the stand-in status like LinuxCNC's: 16 joint entries, some of them real."""
    mock_linuxcnc_stat.joint = [
        dict(mock_linuxcnc_stat.joint[0]) for _ in range(STAT_JOINT_ENTRIES)
    ]
    mock_linuxcnc_stat.joints = configured_joint_count


class TestIndexChecksBeforeDispatch:
    """A joint or spindle that does not exist is refused before anything is sent."""

    @staticmethod
    def send(mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, **command_fields):
        """Send one command; return the response and the command object's calls."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_cmd = MagicMock()
        service = make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, mock_cmd)
        response = service.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(**command_fields), mock_grpc_context
        )
        return response, mock_cmd.method_calls

    @pytest.mark.parametrize("spindle_index", [-1, 1, 99])
    def test_override_config_with_a_bad_spindle_sends_none_of_its_messages(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, spindle_index
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            override_config=linuxcnc_pb2.OverrideConfigCommand(
                feed_override_enable=True, spindle_override_enable=True,
                spindle=spindle_index,
            ),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == f"spindle index {spindle_index} out of range (0..0)"
        assert not response.HasField("linuxcnc_serial")
        assert command_calls == []

    def test_spindle_beyond_the_configured_count_is_refused(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """stat.spindle always has 8 entries; only stat.spindles of them exist."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.spindle = [mock_linuxcnc_stat.spindle[0]] * 8
        mock_linuxcnc_stat.spindles = 1

        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            spindle=linuxcnc_pb2.SpindleControlCommand(
                command=linuxcnc_pb2.SPINDLE_CMD_OFF, spindle=3
            ),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "spindle index 3 out of range (0..0)"
        assert command_calls == []

    def test_override_config_with_a_good_spindle_sends_all_four_messages(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            override_config=linuxcnc_pb2.OverrideConfigCommand(
                feed_override_enable=True, spindle_override_enable=True, spindle=0,
                feed_hold_enable=False, adaptive_feed_enable=True,
            ),
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert command_calls == [
            call.set_feed_override(True),
            call.set_spindle_override(True, 0),
            call.set_feed_hold(False),
            call.set_adaptive_feed(True),
        ]

    @pytest.mark.parametrize("joint_index", [-2, -1, 3, 15, 16])
    def test_set_limit_with_a_bad_joint_sends_neither_limit(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, joint_index
    ):
        """-1 is refused too: "all joints" exists only for home and unhome."""
        from linuxcnc_pb import linuxcnc_pb2

        give_stat_a_full_joint_array(mock_linuxcnc_stat, configured_joint_count=3)
        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            set_limit=linuxcnc_pb2.SetLimitCommand(
                joint=joint_index, min_limit=-10.0, max_limit=10.0
            ),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == f"joint index {joint_index} out of range (0..2)"
        assert not response.HasField("linuxcnc_serial")
        assert command_calls == []

    def test_set_limit_on_the_last_configured_joint_is_sent(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        give_stat_a_full_joint_array(mock_linuxcnc_stat, configured_joint_count=3)
        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            set_limit=linuxcnc_pb2.SetLimitCommand(joint=2, min_limit=-10.0, max_limit=10.0),
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert command_calls == [call.set_min_limit(2, -10.0), call.set_max_limit(2, 10.0)]

    @pytest.mark.parametrize("joint_index", [-1, 3])
    def test_joint_jog_is_bounded_by_the_configured_joint_count(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, joint_index
    ):
        """An unused entry of the 16-entry joint array is not a joint, and neither is -1."""
        from linuxcnc_pb import linuxcnc_pb2

        give_stat_a_full_joint_array(mock_linuxcnc_stat, configured_joint_count=3)
        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_STOP, is_joint=True, index=joint_index
            ),
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == f"joint index {joint_index} out of range (0..2)"
        assert command_calls == []

    @pytest.mark.parametrize("command_name", ["home", "unhome"])
    def test_home_and_unhome_are_bounded_by_the_configured_joint_count(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, command_name
    ):
        from linuxcnc_pb import linuxcnc_pb2

        give_stat_a_full_joint_array(mock_linuxcnc_stat, configured_joint_count=3)
        joint_command = (
            linuxcnc_pb2.HomeCommand(joint=3) if command_name == "home"
            else linuxcnc_pb2.UnhomeCommand(joint=3)
        )
        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            **{command_name: joint_command},
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message == "joint index 3 out of range (0..2)"
        assert command_calls == []

    def test_home_all_joints_is_still_sent(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        give_stat_a_full_joint_array(mock_linuxcnc_stat, configured_joint_count=3)
        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            home=linuxcnc_pb2.HomeCommand(joint=-1),
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert command_calls == [call.home(-1)]

    @pytest.mark.parametrize("unusable_joint_count", [None, 0, True, "3", 99])
    def test_joint_count_that_is_not_usable_falls_back_to_the_array_length(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        unusable_joint_count,
    ):
        """Without a believable stat.joints the old bound, the array length, applies."""
        from linuxcnc_pb import linuxcnc_pb2

        give_stat_a_full_joint_array(mock_linuxcnc_stat, unusable_joint_count)
        response, command_calls = self.send(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
            jog=linuxcnc_pb2.JogCommand(type=linuxcnc_pb2.JOG_STOP, is_joint=True, index=15),
        )

        assert response.status == linuxcnc_pb2.RCS_DONE
        assert command_calls == [call.jog(0, True, 15)]


class TestWaitCompleteTimeoutValues:
    """Timeouts that are not ordinary numbers of seconds."""

    @staticmethod
    def _service_with_one_command_sent(
        mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        command = FakeLinuxCNCCommand(mock_linuxcnc_stat)
        service = TestWaitComplete._make_service(
            mock_linuxcnc_module, mock_linuxcnc_stat, command
        )
        service.SendCommand(TestWaitComplete._mdi_request("G1 X5 F5"), mock_grpc_context)
        return service

    @pytest.mark.parametrize("huge_timeout", [float("inf"), 1e300, 1e12])
    def test_huge_or_infinite_timeout_waits_instead_of_failing(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, huge_timeout
    ):
        """Such a timeout is more than a lock wait accepts in one piece."""
        from linuxcnc_pb import linuxcnc_pb2

        service = self._service_with_one_command_sent(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
        )
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_DONE

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(timeout=huge_timeout), mock_grpc_context
        )

        assert response.status == linuxcnc_pb2.RCS_DONE

    def test_infinite_wait_ends_when_the_servicer_closes(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        import time
        from linuxcnc_pb import linuxcnc_pb2

        service = self._service_with_one_command_sent(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
        )
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC
        sleeps_before_close = []

        def close_during_the_first_sleep(duration):
            sleeps_before_close.append(duration)
            assert len(sleeps_before_close) == 1, "the wait went on after close()"
            service.close()

        with patch.object(time, "sleep", side_effect=close_during_the_first_sleep):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(timeout=float("inf")), mock_grpc_context
            )

        assert response.status == linuxcnc_pb2.RCS_EXEC
        assert response.error_message == ""

    def test_nan_timeout_means_the_default_like_zero(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        import time
        from linuxcnc_pb import linuxcnc_pb2

        service = self._service_with_one_command_sent(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
        )
        mock_linuxcnc_stat.state = mock_linuxcnc_module.RCS_EXEC

        # Fake clock: every sleep advances monotonic time by the slept amount.
        fake_clock = {"now": 1000.0}
        slept_durations = []

        def fake_sleep(duration):
            slept_durations.append(duration)
            fake_clock["now"] += duration

        with patch.object(
            time, "monotonic", side_effect=lambda: fake_clock["now"]
        ), patch.object(time, "sleep", side_effect=fake_sleep):
            response = service.WaitComplete(
                linuxcnc_pb2.WaitCompleteRequest(timeout=float("nan")), mock_grpc_context
            )

        assert response.status == linuxcnc_pb2.RCS_EXEC
        assert sum(slept_durations) == pytest.approx(5.0)

    def test_lock_wait_with_an_infinite_deadline_outlasts_a_busy_lock(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        import math
        import threading

        service = self._service_with_one_command_sent(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
        )
        observed_lock = LockThatReportsFailedAttempts(service._lock)
        service._lock = observed_lock
        lock_is_held = threading.Event()
        release_the_lock = threading.Event()

        def hold_the_lock():
            with service._lock:
                lock_is_held.set()
                release_the_lock.wait(30)

        acquire_results = []

        def acquire_with_infinite_deadline():
            try:
                acquired = service._acquire_lock_until(math.inf, mock_grpc_context)
            except Exception as acquire_error:
                acquire_results.append(acquire_error)
                return
            acquire_results.append(acquired)
            service._lock.release()

        holder_thread = threading.Thread(target=hold_the_lock, daemon=True)
        holder_thread.start()
        assert lock_is_held.wait(10)
        waiter_thread = threading.Thread(target=acquire_with_infinite_deadline, daemon=True)
        waiter_thread.start()
        try:
            # The lock is released only after the waiter has been seen to
            # fail an attempt at it, so the waiter met a busy lock and went
            # on trying.
            waiter_was_blocked = observed_lock.an_attempt_failed.wait(10)
            waiter_gave_up = bool(acquire_results)
        finally:
            release_the_lock.set()
            waiter_thread.join(10)
            holder_thread.join(10)

        assert waiter_was_blocked, "the waiter never had to wait for the lock"
        assert not waiter_gave_up, "a failed attempt ended the wait"
        assert acquire_results == [True]

    def test_free_lock_is_taken_even_for_a_cancelled_rpc_past_its_deadline(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        import time

        service = self._service_with_one_command_sent(
            mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
        )
        mock_grpc_context.is_active.return_value = False

        acquired = service._acquire_lock_until(time.monotonic() - 1.0, mock_grpc_context)

        assert acquired
        service._lock.release()


class TestStreamStatusWaiting:
    """How StreamStatus waits between updates, and what ends the wait."""

    @staticmethod
    def _make_service(mock_linuxcnc_module, mock_linuxcnc_stat):
        return make_service_with_command(mock_linuxcnc_module, mock_linuxcnc_stat, MagicMock())

    @pytest.mark.parametrize(
        "requested_interval_ms, expected_wait_seconds",
        [
            (250, 0.25),
            (60_000, 60.0),
            (60_001, 60.0),
            (2_147_483_647, 60.0),
            (-5, 0.1),
        ],
    )
    def test_interval_is_bounded(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context,
        requested_interval_ms, expected_wait_seconds,
    ):
        """An interval above the maximum is shortened to it, not refused."""
        from linuxcnc_pb import linuxcnc_pb2

        import time

        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat)
        mock_grpc_context.is_active.side_effect = [True, True, False]
        waited_seconds = record_stream_waits(mock_grpc_context)

        # A stream that slept instead of waiting on its event would stall
        # the test run for the whole interval; make that fail quickly.
        with patch.object(time, "sleep") as unexpected_sleep:
            statuses = list(
                service.StreamStatus(
                    linuxcnc_pb2.StreamStatusRequest(interval_ms=requested_interval_ms),
                    mock_grpc_context,
                )
            )

        unexpected_sleep.assert_not_called()
        assert len(statuses) == 2
        mock_grpc_context.abort.assert_not_called()
        assert waited_seconds == [expected_wait_seconds, expected_wait_seconds]

    @staticmethod
    def _stream_in_a_thread(service, mock_grpc_context, interval_ms):
        """
        Start a stream on its own thread, as a server worker would run it.

        Returns the thread, the list its statuses are appended to, an event
        set once the first status has arrived, and the list that receives
        the callback the stream registered for the end of the RPC.
        """
        import threading
        from linuxcnc_pb import linuxcnc_pb2

        registered_callbacks = []

        def register_callback(rpc_ended_callback):
            registered_callbacks.append(rpc_ended_callback)
            return True

        mock_grpc_context.add_callback.side_effect = register_callback
        received_statuses = []
        first_status_received = threading.Event()

        def consume_stream():
            for status in service.StreamStatus(
                linuxcnc_pb2.StreamStatusRequest(interval_ms=interval_ms), mock_grpc_context
            ):
                received_statuses.append(status)
                first_status_received.set()

        stream_thread = threading.Thread(target=consume_stream, daemon=True)
        stream_thread.start()
        return stream_thread, received_statuses, first_status_received, registered_callbacks

    def test_client_cancelling_ends_a_long_wait_at_once(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat)
        stream_thread, received_statuses, first_status_received, registered_callbacks = (
            self._stream_in_a_thread(service, mock_grpc_context, interval_ms=60_000)
        )
        assert first_status_received.wait(10)

        # What gRPC does when the client cancels or its connection drops.
        registered_callbacks[0]()
        stream_thread.join(10)

        assert not stream_thread.is_alive(), "the stream kept waiting after the RPC ended"
        assert len(received_statuses) == 1
        assert service._stream_wake_events == set()

    def test_closing_the_servicer_ends_a_long_wait_at_once(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat)
        stream_thread, received_statuses, first_status_received, _ = (
            self._stream_in_a_thread(service, mock_grpc_context, interval_ms=60_000)
        )
        assert first_status_received.wait(10)

        service.close()
        stream_thread.join(10)

        assert not stream_thread.is_alive(), "the stream kept waiting after close()"
        assert len(received_statuses) == 1

    def test_stream_started_after_close_sends_nothing(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        import time

        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat)
        service.close()
        # Bounded, so a stream that did start ends instead of running on.
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.object(time, "sleep"):
            statuses = list(
                service.StreamStatus(
                    linuxcnc_pb2.StreamStatusRequest(interval_ms=60_000), mock_grpc_context
                )
            )

        assert statuses == []
        assert service._stream_wake_events == set()

    def test_rpc_that_is_already_over_sends_nothing(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """add_callback returns False when the RPC has already ended."""
        from linuxcnc_pb import linuxcnc_pb2

        import time

        service = self._make_service(mock_linuxcnc_module, mock_linuxcnc_stat)
        mock_grpc_context.add_callback.return_value = False
        # Bounded, so a stream that did start ends instead of running on.
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.object(time, "sleep"):
            statuses = list(
                service.StreamStatus(
                    linuxcnc_pb2.StreamStatusRequest(interval_ms=60_000), mock_grpc_context
                )
            )

        assert statuses == []
        assert service._stream_wake_events == set()


def context_whose_abort_raises():
    """
    A context that behaves like real gRPC: abort() raises a bare Exception.

    Every abort is still recorded, so a test can check that an RPC aborted
    exactly once and that no handler caught the abort and aborted again.
    """
    context = MagicMock(spec=grpc.ServicerContext)
    context.is_active.return_value = True
    context.abort.side_effect = Exception()
    return context


def abort_codes(context):
    """The status code of every abort the context received, in order."""
    return [abort_call.args[0] for abort_call in context.abort.call_args_list]


NULL_WITHOUT_EXCEPTION = "error return without exception set"


class TestUnexpectedPollErrors:
    """
    A status poll that fails with something other than linuxcnc.error.

    The RPCs that poll answer INTERNAL (WaitComplete: RCS_ERROR) instead of
    letting the exception reach the client as UNKNOWN, and their deliberate
    aborts keep their own status codes.
    """

    @pytest.fixture
    def service(self, mock_linuxcnc_module, mock_linuxcnc_stat, tmp_path):
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            sys.modules.pop("linuxcnc_grpc.machine_config", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

            servicer = LinuxCNCServiceServicer(nc_files_dir=str(tmp_path))
        # A reconnect would hide the result under test and must not happen.
        servicer._reconnect = MagicMock(return_value=False)
        yield servicer
        servicer.close()

    @staticmethod
    def _assert_single_internal_abort(context, rpc_name, error_type_name, error_text):
        assert abort_codes(context) == [grpc.StatusCode.INTERNAL]
        details = context.abort.call_args.args[1]
        assert details == f"{rpc_name} failed: {error_type_name}: {error_text}"

    # ---- GetStatus -------------------------------------------------------

    @pytest.mark.parametrize(
        "unexpected_error",
        [SystemError(NULL_WITHOUT_EXCEPTION), ValueError("unknown task mode 99")],
    )
    def test_get_status_is_internal(self, service, mock_linuxcnc_stat, unexpected_error):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = unexpected_error

        with pytest.raises(Exception) as raised:
            service.GetStatus(linuxcnc_pb2.GetStatusRequest(), context)

        # What left the RPC is the abort, not the poll's exception.
        assert raised.value is context.abort.side_effect
        self._assert_single_internal_abort(
            context, "GetStatus", type(unexpected_error).__name__, str(unexpected_error)
        )
        service._reconnect.assert_not_called()

    def test_get_status_linuxcnc_error_is_still_unavailable(
        self, service, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("NML gone")

        with pytest.raises(Exception):
            service.GetStatus(linuxcnc_pb2.GetStatusRequest(), context)

        assert abort_codes(context) == [grpc.StatusCode.UNAVAILABLE]

    def test_get_status_while_disconnected_is_still_unavailable(self, service):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        service._connected = False

        with pytest.raises(Exception):
            service.GetStatus(linuxcnc_pb2.GetStatusRequest(), context)

        assert abort_codes(context) == [grpc.StatusCode.UNAVAILABLE]

    # ---- WaitComplete ----------------------------------------------------

    def test_wait_complete_is_rcs_error(self, service, mock_linuxcnc_stat):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = SystemError(NULL_WITHOUT_EXCEPTION)

        response = service.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(serial=7, timeout=1.0), context
        )

        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.serial == 7
        assert response.error_message == (
            f"WaitComplete failed: SystemError: {NULL_WITHOUT_EXCEPTION}"
        )
        context.abort.assert_not_called()
        service._reconnect.assert_not_called()

    def test_wait_complete_while_disconnected_is_still_unavailable(self, service):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        service._connected = False

        with pytest.raises(Exception):
            service.WaitComplete(linuxcnc_pb2.WaitCompleteRequest(timeout=1.0), context)

        assert abort_codes(context) == [grpc.StatusCode.UNAVAILABLE]

    # ---- StreamStatus ----------------------------------------------------

    def test_stream_status_is_internal_after_the_statuses_already_sent(
        self, service, mock_linuxcnc_stat
    ):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = [None, SystemError(NULL_WITHOUT_EXCEPTION)]
        stream = service.StreamStatus(linuxcnc_pb2.StreamStatusRequest(interval_ms=1), context)

        first_status = next(stream)
        with pytest.raises(Exception) as raised:
            next(stream)

        assert first_status.timestamp > 0
        assert raised.value is context.abort.side_effect
        self._assert_single_internal_abort(
            context, "StreamStatus", "SystemError", NULL_WITHOUT_EXCEPTION
        )
        service._reconnect.assert_not_called()
        assert service._stream_wake_events == set()

    def test_stream_status_linuxcnc_error_is_still_unavailable(
        self, service, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("NML gone")
        stream = service.StreamStatus(linuxcnc_pb2.StreamStatusRequest(interval_ms=1), context)

        with pytest.raises(Exception):
            next(stream)

        assert abort_codes(context) == [grpc.StatusCode.UNAVAILABLE]
        assert service._stream_wake_events == set()

    def test_stream_status_os_error_keeps_its_message(self, service, mock_linuxcnc_stat):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = OSError("pipe closed")
        stream = service.StreamStatus(linuxcnc_pb2.StreamStatusRequest(interval_ms=1), context)

        with pytest.raises(Exception):
            next(stream)

        assert context.abort.call_args_list == [
            call(grpc.StatusCode.INTERNAL, "Stream failed: pipe closed")
        ]

    # ---- ResolveFile -----------------------------------------------------

    def test_resolve_file_os_error_keeps_its_message(self, service, mock_linuxcnc_stat):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = OSError("pipe closed")

        with pytest.raises(Exception):
            service.ResolveFile(linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), context)

        assert context.abort.call_args_list == [
            call(grpc.StatusCode.INTERNAL, "Failed to resolve file: pipe closed")
        ]

    def test_resolve_file_is_internal(self, service, mock_linuxcnc_stat):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = SystemError(NULL_WITHOUT_EXCEPTION)

        with pytest.raises(Exception) as raised:
            service.ResolveFile(linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), context)

        assert raised.value is context.abort.side_effect
        self._assert_single_internal_abort(
            context, "ResolveFile", "SystemError", NULL_WITHOUT_EXCEPTION
        )
        service._reconnect.assert_not_called()

    def test_resolve_file_deliberate_aborts_keep_their_codes(
        self, service, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_pb import linuxcnc_pb2

        bad_name_context = context_whose_abort_raises()
        with pytest.raises(Exception):
            service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="../outside.ngc"), bad_name_context
            )
        assert abort_codes(bad_name_context) == [grpc.StatusCode.INVALID_ARGUMENT]

        linuxcnc_error_context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("NML gone")
        with pytest.raises(Exception):
            service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), linuxcnc_error_context
            )
        assert abort_codes(linuxcnc_error_context) == [grpc.StatusCode.UNAVAILABLE]

        disconnected_context = context_whose_abort_raises()
        service._connected = False
        with pytest.raises(Exception):
            service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), disconnected_context
            )
        assert abort_codes(disconnected_context) == [grpc.StatusCode.UNAVAILABLE]

    # ---- GetMachineConfig ------------------------------------------------

    def test_get_machine_config_is_internal(self, service, mock_linuxcnc_stat):
        from linuxcnc_pb import linuxcnc_pb2

        context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = SystemError(NULL_WITHOUT_EXCEPTION)

        with pytest.raises(Exception) as raised:
            service.GetMachineConfig(linuxcnc_pb2.GetMachineConfigRequest(), context)

        assert raised.value is context.abort.side_effect
        self._assert_single_internal_abort(
            context, "GetMachineConfig", "SystemError", NULL_WITHOUT_EXCEPTION
        )
        service._reconnect.assert_not_called()

    def test_get_machine_config_deliberate_aborts_keep_their_codes(
        self, service, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_pb import linuxcnc_pb2

        linuxcnc_error_context = context_whose_abort_raises()
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("NML gone")
        with pytest.raises(Exception):
            service.GetMachineConfig(
                linuxcnc_pb2.GetMachineConfigRequest(), linuxcnc_error_context
            )
        assert abort_codes(linuxcnc_error_context) == [grpc.StatusCode.UNAVAILABLE]

        disconnected_context = context_whose_abort_raises()
        service._connected = False
        with pytest.raises(Exception):
            service.GetMachineConfig(
                linuxcnc_pb2.GetMachineConfigRequest(), disconnected_context
            )
        assert abort_codes(disconnected_context) == [grpc.StatusCode.UNAVAILABLE]
