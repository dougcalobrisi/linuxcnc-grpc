"""
Unit tests for LinuxCNCMapper class.

LinuxCNCMapper requires the linuxcnc module, so we mock it before import.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest


# We need to mock linuxcnc before importing the mapper
@pytest.fixture(autouse=True)
def mock_linuxcnc_import(mock_linuxcnc_module):
    """Mock the linuxcnc module before any imports."""
    with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
        yield


class TestLinuxCNCMapperImport:
    """Test that LinuxCNCMapper can be imported with mocked linuxcnc."""

    def test_import_mapper(self, mock_linuxcnc_module):
        """Mapper imports successfully with mocked linuxcnc."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            assert LinuxCNCMapper is not None


class TestLinuxCNCMapperPosition:
    """Test position mapping."""

    def test_map_position_basic(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map a 9-element position tuple."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            pos = mapper._map_position((1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

            assert pos.x == 1.0
            assert pos.y == 2.0
            assert pos.z == 3.0
            assert pos.a == 0.0
            assert pos.b == 0.0
            assert pos.c == 0.0
            assert pos.u == 0.0
            assert pos.v == 0.0
            assert pos.w == 0.0

    def test_map_position_with_rotary(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map position with rotary axes."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            pos = mapper._map_position((10.0, 20.0, 30.0, 45.0, 90.0, 180.0, 0.0, 0.0, 0.0))

            assert pos.x == 10.0
            assert pos.y == 20.0
            assert pos.z == 30.0
            assert pos.a == 45.0
            assert pos.b == 90.0
            assert pos.c == 180.0


class TestLinuxCNCMapperEnums:
    """Test enum mapping functions."""

    def test_map_task_mode_manual(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map MODE_MANUAL."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_mode(mock_linuxcnc_module.MODE_MANUAL)
            assert result == linuxcnc_pb2.MODE_MANUAL

    def test_map_task_mode_auto(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map MODE_AUTO."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_mode(mock_linuxcnc_module.MODE_AUTO)
            assert result == linuxcnc_pb2.MODE_AUTO

    def test_map_task_mode_mdi(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map MODE_MDI."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_mode(mock_linuxcnc_module.MODE_MDI)
            assert result == linuxcnc_pb2.MODE_MDI

    def test_map_task_state_estop(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map STATE_ESTOP."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_state(mock_linuxcnc_module.STATE_ESTOP)
            assert result == linuxcnc_pb2.STATE_ESTOP

    def test_map_task_state_on(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map STATE_ON."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_state(mock_linuxcnc_module.STATE_ON)
            assert result == linuxcnc_pb2.STATE_ON

    def test_map_rcs_status_done(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map RCS_DONE."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_rcs_status(mock_linuxcnc_module.RCS_DONE)
            assert result == linuxcnc_pb2.RCS_DONE

    def test_map_interp_state_idle(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map INTERP_IDLE."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_interp_state(mock_linuxcnc_module.INTERP_IDLE)
            assert result == linuxcnc_pb2.INTERP_IDLE

    def test_map_joint_type_linear(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map LINEAR joint type."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_joint_type(mock_linuxcnc_module.LINEAR)
            assert result == linuxcnc_pb2.JOINT_LINEAR

    def test_map_joint_type_angular(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map ANGULAR joint type."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_joint_type(mock_linuxcnc_module.ANGULAR)
            assert result == linuxcnc_pb2.JOINT_ANGULAR


class TestLinuxCNCMapperCoolant:
    """Test coolant state mapping."""

    def test_map_coolant_on(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map coolant ON state."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_coolant_state(1)
            assert result == linuxcnc_pb2.COOLANT_ON

    def test_map_coolant_off(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map coolant OFF state."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_coolant_state(0)
            assert result == linuxcnc_pb2.COOLANT_OFF


class TestLinuxCNCMapperFullStatus:
    """Test full status mapping."""

    def test_map_to_proto(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map full LinuxCNC status to protobuf."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            # Verify basic fields
            assert status.timestamp > 0
            assert status.version == "2.9"

            # Verify task status
            assert status.task.task_mode == linuxcnc_pb2.MODE_MANUAL
            assert status.task.task_state == linuxcnc_pb2.STATE_ON
            assert status.task.exec_state == linuxcnc_pb2.EXEC_DONE
            assert status.task.interp_state == linuxcnc_pb2.INTERP_IDLE

            # Verify trajectory
            assert status.trajectory.enabled is True
            assert status.trajectory.joints == 3
            assert status.trajectory.spindles == 1

            # Verify joints
            assert len(status.joints) == 3
            assert status.joints[0].homed is True
            assert status.joints[0].enabled is True

            # Verify position
            assert status.position.actual_position.x == 1.0
            assert status.position.actual_position.y == 2.0
            assert status.position.actual_position.z == 3.0

    def test_map_to_proto_joints(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Verify joint mapping details."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            joint0 = status.joints[0]
            assert joint0.joint_number == 0
            assert joint0.joint_type == linuxcnc_pb2.JOINT_LINEAR
            assert joint0.min_position_limit == -100.0
            assert joint0.max_position_limit == 100.0
            assert joint0.input == 1.0

    def test_map_to_proto_spindles(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Verify spindle mapping."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            assert len(status.spindles) == 1
            spindle = status.spindles[0]
            assert spindle.spindle_number == 0
            assert spindle.enabled is False
            assert spindle.override == 1.0

    def test_map_to_proto_io(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Verify IO mapping."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_grpc_server._generated import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            assert status.io.estop is False
            assert status.io.mist == linuxcnc_pb2.COOLANT_OFF
            assert status.io.flood == linuxcnc_pb2.COOLANT_OFF
            assert len(status.io.analog_inputs) == 16
            assert len(status.io.digital_inputs) == 16

    def test_map_to_proto_gcodes(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Verify G-code status mapping."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            assert len(status.gcode.active_gcodes) > 0
            assert len(status.gcode.active_mcodes) > 0
            assert status.gcode.feed_rate == 100.0
            assert status.gcode.spindle_speed == 1000.0


class TestLinuxCNCMapperLimits:
    """Test limit status mapping."""

    def test_map_limit_status(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Verify limit status mapping."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc_server.linuxcnc_mapper import LinuxCNCMapper

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            assert len(status.limits.limit_flags) == 3
            assert len(status.limits.homed) == 3
            # All joints homed in mock
            assert all(h == 1 for h in status.limits.homed)
