"""
Unit tests for LinuxCNCServiceServicer.

Tests the gRPC service layer for LinuxCNC machine control.
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import grpc
import pytest


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
             patch.dict(os.environ, {"LINUXCNC_NC_FILES": "/home/cnc/nc_files"}):
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


class TestWaitComplete:
    """Test WaitComplete RPC."""

    def test_rcs_done(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns RCS_DONE when complete."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_cmd.wait_complete.return_value = 1  # RCS_DONE
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.RCS_DONE = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.WaitCompleteRequest(serial=1, timeout=5.0)
            response = service.WaitComplete(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_DONE
            assert response.error_message == ""

    def test_rcs_error(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Returns RCS_ERROR on failure."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_cmd.wait_complete.return_value = 3  # RCS_ERROR
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.RCS_DONE = 1
        mock_linuxcnc_module.RCS_ERROR = 3

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.WaitCompleteRequest(serial=1, timeout=5.0)
            response = service.WaitComplete(request, mock_grpc_context)

            assert response.status == linuxcnc_pb2.RCS_ERROR

    def test_default_timeout(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Uses 5.0s default timeout when not specified."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_cmd = MagicMock()
        mock_cmd.wait_complete.return_value = 1
        mock_linuxcnc_module.command.return_value = mock_cmd
        mock_linuxcnc_module.error_channel.return_value = MagicMock()
        mock_linuxcnc_module.RCS_DONE = 1

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.WaitCompleteRequest(serial=1, timeout=0)
            service.WaitComplete(request, mock_grpc_context)

            mock_cmd.wait_complete.assert_called_once_with(5.0)


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

            # Patch time.sleep after import
            with patch("linuxcnc_grpc.linuxcnc_service.time.sleep") as mock_sleep:
                service = LinuxCNCServiceServicer()
                request = linuxcnc_pb2.StreamStatusRequest(interval_ms=0)

                list(service.StreamStatus(request, mock_grpc_context))

                mock_sleep.assert_called_with(0.1)


class TestStreamErrors:
    """Test StreamErrors RPC."""

    def test_yields_error_messages(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Yields error messages from channel."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_error_channel = MagicMock()
        # Return error first time, None second, then stop
        mock_error_channel.poll.side_effect = [(1, "Test error"), None]
        mock_linuxcnc_module.error_channel.return_value = mock_error_channel
        mock_linuxcnc_module.OPERATOR_ERROR = 1
        mock_grpc_context.is_active.side_effect = [True, True, False]

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.StreamErrorsRequest()

            results = list(service.StreamErrors(request, mock_grpc_context))

            assert len(results) == 1
            assert results[0].message == "Test error"

    def test_no_error_no_yield(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Does not yield when no errors."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_error_channel = MagicMock()
        mock_error_channel.poll.return_value = None
        mock_linuxcnc_module.error_channel.return_value = mock_error_channel
        mock_grpc_context.is_active.side_effect = [True, False]

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.StreamErrorsRequest()

            results = list(service.StreamErrors(request, mock_grpc_context))

            assert len(results) == 0

    def test_yields_different_error_types(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Yields different error types."""
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_error_channel = MagicMock()
        mock_error_channel.poll.side_effect = [
            (1, "Error message"),
            (2, "Text message"),
            None,
        ]
        mock_linuxcnc_module.error_channel.return_value = mock_error_channel
        mock_linuxcnc_module.OPERATOR_ERROR = 1
        mock_linuxcnc_module.OPERATOR_TEXT = 2
        mock_grpc_context.is_active.side_effect = [True, True, True, False]

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer
            from linuxcnc_pb import linuxcnc_pb2

            service = LinuxCNCServiceServicer()
            request = linuxcnc_pb2.StreamErrorsRequest()

            results = list(service.StreamErrors(request, mock_grpc_context))

            assert len(results) == 2
            assert results[0].message == "Error message"
            assert results[1].message == "Text message"


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
