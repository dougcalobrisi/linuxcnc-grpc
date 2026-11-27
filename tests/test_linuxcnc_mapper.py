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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            assert LinuxCNCMapper is not None


class TestLinuxCNCMapperPosition:
    """Test position mapping."""

    def test_map_position_basic(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map a 9-element position tuple."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_mode(mock_linuxcnc_module.MODE_MANUAL)
            assert result == linuxcnc_pb2.MODE_MANUAL

    def test_map_task_mode_auto(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map MODE_AUTO."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_mode(mock_linuxcnc_module.MODE_AUTO)
            assert result == linuxcnc_pb2.MODE_AUTO

    def test_map_task_mode_mdi(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map MODE_MDI."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_mode(mock_linuxcnc_module.MODE_MDI)
            assert result == linuxcnc_pb2.MODE_MDI

    def test_map_task_state_estop(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map STATE_ESTOP."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_state(mock_linuxcnc_module.STATE_ESTOP)
            assert result == linuxcnc_pb2.STATE_ESTOP

    def test_map_task_state_on(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map STATE_ON."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_task_state(mock_linuxcnc_module.STATE_ON)
            assert result == linuxcnc_pb2.STATE_ON

    def test_map_rcs_status_done(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map RCS_DONE."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_rcs_status(mock_linuxcnc_module.RCS_DONE)
            assert result == linuxcnc_pb2.RCS_DONE

    def test_map_interp_state_idle(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map INTERP_IDLE."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_interp_state(mock_linuxcnc_module.INTERP_IDLE)
            assert result == linuxcnc_pb2.INTERP_IDLE

    def test_map_joint_type_linear(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map LINEAR joint type."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_joint_type(mock_linuxcnc_module.LINEAR)
            assert result == linuxcnc_pb2.JOINT_LINEAR

    def test_map_joint_type_angular(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map ANGULAR joint type."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_joint_type(mock_linuxcnc_module.ANGULAR)
            assert result == linuxcnc_pb2.JOINT_ANGULAR


class TestLinuxCNCMapperCoolant:
    """Test coolant state mapping."""

    def test_map_coolant_on(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map coolant ON state."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_coolant_state(1)
            assert result == linuxcnc_pb2.COOLANT_ON

    def test_map_coolant_off(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map coolant OFF state."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            result = mapper._map_coolant_state(0)
            assert result == linuxcnc_pb2.COOLANT_OFF


class TestLinuxCNCMapperFullStatus:
    """Test full status mapping."""

    def test_map_to_proto(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Map full LinuxCNC status to protobuf."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

            # Pass version from mock module (simulates real linuxcnc.version)
            mapper = LinuxCNCMapper(mock_linuxcnc_stat, version=mock_linuxcnc_module.version)
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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper
            from linuxcnc_pb import linuxcnc_pb2

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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

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
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

            mapper = LinuxCNCMapper(mock_linuxcnc_stat)
            status = mapper.map_to_proto()

            assert len(status.limits.limit_flags) == 3
            assert len(status.limits.homed) == 3
            # All joints homed in mock
            assert all(h == 1 for h in status.limits.homed)

    def test_misc_error_carries_the_real_values(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """misc_error is LinuxCNC's own list, not one zero per joint."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

            # The binding returns a tuple of ints, longer than the joint list.
            mock_linuxcnc_stat.misc_error = (0, 7, 0, -3, 0, 0, 0, 0)

            status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

            assert list(status.limits.misc_error) == [0, 7, 0, -3, 0, 0, 0, 0]


# Status attributes LinuxCNC 2.9 has and 2.8 does not.
ATTRIBUTES_MISSING_IN_LINUXCNC_2_8 = (
    "ini_filename", "num_extrajoints", "tool_from_pocket", "motion_id", "misc_error",
)


def status_shaped_like_linuxcnc_2_8(mock_linuxcnc_stat):
    """
    A strict copy of the status fixture with only the attributes 2.8 has.

    Unlike the fixture (a MagicMock, which invents any attribute asked for),
    reading anything that was not set raises AttributeError, as the real
    status object does. 2.8 reports the motion id as ``id``.
    """
    from types import SimpleNamespace

    attributes_the_fixture_set = {
        attribute_name: attribute_value
        for attribute_name, attribute_value in vars(mock_linuxcnc_stat).items()
        if not attribute_name.startswith("_mock") and attribute_name != "method_calls"
    }
    for attribute_name in ATTRIBUTES_MISSING_IN_LINUXCNC_2_8:
        del attributes_the_fixture_set[attribute_name]
    attributes_the_fixture_set["id"] = 42
    return SimpleNamespace(**attributes_the_fixture_set)


class TestLinuxCNCMapperOnLinuxCNC28:
    """A status without the attributes LinuxCNC 2.8 lacks still maps."""

    def test_parts_that_2_8_also_has_map_the_same(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """The mapper reads nothing else that the 2.8-shaped status lacks."""
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

            status_2_8 = status_shaped_like_linuxcnc_2_8(mock_linuxcnc_stat)
            for attribute_name in ATTRIBUTES_MISSING_IN_LINUXCNC_2_8:
                assert not hasattr(status_2_8, attribute_name)

            mapped_2_8 = LinuxCNCMapper(status_2_8).map_to_proto()
            mapped_2_9 = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

            assert mapped_2_8.position == mapped_2_9.position
            assert mapped_2_8.joints == mapped_2_9.joints
            assert mapped_2_8.io == mapped_2_9.io

    def test_missing_attributes_become_proto_defaults(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

            status = LinuxCNCMapper(
                status_shaped_like_linuxcnc_2_8(mock_linuxcnc_stat)
            ).map_to_proto()

            assert status.task.ini_filename == ""
            assert status.trajectory.num_extrajoints == 0
            assert status.tool.tool_from_pocket == 0
            assert list(status.limits.misc_error) == []
            # The rest of the status is still there.
            assert status.trajectory.joints == 3
            assert status.tool.tool_in_spindle == 1
            assert len(status.limits.homed) == 3

    def test_motion_id_falls_back_to_the_name_2_8_uses(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

            status = LinuxCNCMapper(
                status_shaped_like_linuxcnc_2_8(mock_linuxcnc_stat)
            ).map_to_proto()

            assert status.trajectory.motion_id == 42

    def test_motion_id_prefers_the_2_9_name(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

            mock_linuxcnc_stat.motion_id = 17
            mock_linuxcnc_stat.id = 99

            status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

            assert status.trajectory.motion_id == 17


# LinuxCNC's status always carries this many joint and spindle entries,
# whatever the machine has (measured on the 2.9 simulator).
STAT_JOINT_ENTRIES = 16
STAT_SPINDLE_ENTRIES = 8


def give_stat_full_length_arrays(mock_linuxcnc_stat, real_joint_count, real_spindle_count):
    """Shape the stand-in status like LinuxCNC's: 16 joint and 8 spindle entries."""
    mock_linuxcnc_stat.joint = [
        dict(mock_linuxcnc_stat.joint[0]) for _ in range(STAT_JOINT_ENTRIES)
    ]
    mock_linuxcnc_stat.spindle = [
        dict(mock_linuxcnc_stat.spindle[0]) for _ in range(STAT_SPINDLE_ENTRIES)
    ]
    # The last real joint sits on its upper hard limit and the slots after it
    # claim to be homed, so a slot that leaks through would show.
    mock_linuxcnc_stat.joint[real_joint_count - 1]["max_hard_limit"] = True
    mock_linuxcnc_stat.homed = tuple(
        0 if joint_index < real_joint_count else 1
        for joint_index in range(STAT_JOINT_ENTRIES)
    )
    mock_linuxcnc_stat.joints = real_joint_count
    mock_linuxcnc_stat.spindles = real_spindle_count


class TestOnlyRealJointsAndSpindles:
    """The status lists the machine's joints and spindles, not LinuxCNC's spare slots."""

    def test_joints_and_spindles_stop_at_the_machine_counts(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        give_stat_full_length_arrays(
            mock_linuxcnc_stat, real_joint_count=3, real_spindle_count=1
        )

        status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

        assert [joint.joint_number for joint in status.joints] == [0, 1, 2]
        assert [spindle.spindle_number for spindle in status.spindles] == [0]
        assert status.trajectory.joints == 3
        assert status.trajectory.spindles == 1

    def test_per_joint_limit_lists_stop_at_the_joint_count(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        give_stat_full_length_arrays(
            mock_linuxcnc_stat, real_joint_count=3, real_spindle_count=1
        )

        status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

        assert list(status.limits.limit_flags) == [0, 0, 2]
        assert list(status.limits.homed) == [False, False, False]

    def test_misc_error_and_axes_keep_their_own_length(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        give_stat_full_length_arrays(
            mock_linuxcnc_stat, real_joint_count=2, real_spindle_count=1
        )
        mock_linuxcnc_stat.misc_error = (0, 7, 0, -3, 0, 0, 0, 0)

        status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

        assert len(status.joints) == 2
        assert list(status.limits.misc_error) == [0, 7, 0, -3, 0, 0, 0, 0]
        assert len(status.axes) == len(mock_linuxcnc_stat.axis)

    def test_a_machine_using_every_slot_lists_them_all(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        give_stat_full_length_arrays(
            mock_linuxcnc_stat,
            real_joint_count=STAT_JOINT_ENTRIES, real_spindle_count=STAT_SPINDLE_ENTRIES,
        )

        status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

        assert len(status.joints) == STAT_JOINT_ENTRIES
        assert len(status.spindles) == STAT_SPINDLE_ENTRIES
        assert len(status.limits.limit_flags) == STAT_JOINT_ENTRIES
        assert len(status.limits.homed) == STAT_JOINT_ENTRIES

    @pytest.mark.parametrize("unusable_count", [0, -1, 17, 99])
    def test_a_count_that_cannot_be_right_keeps_the_full_lists(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, unusable_count
    ):
        """Better to list spare slots than to drop a joint the machine has."""
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        give_stat_full_length_arrays(
            mock_linuxcnc_stat, real_joint_count=3, real_spindle_count=1
        )
        mock_linuxcnc_stat.joints = unusable_count
        mock_linuxcnc_stat.spindles = unusable_count

        status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto()

        assert len(status.joints) == STAT_JOINT_ENTRIES
        assert len(status.spindles) == STAT_SPINDLE_ENTRIES
        assert len(status.limits.limit_flags) == STAT_JOINT_ENTRIES
        assert len(status.limits.homed) == STAT_JOINT_ENTRIES

    @pytest.mark.parametrize("not_a_plain_int", [True, 3.0, "3", None, MagicMock()])
    def test_a_count_that_is_not_a_plain_int_means_the_full_list(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, not_a_plain_int
    ):
        from linuxcnc_grpc.linuxcnc_mapper import real_entry_count

        assert real_entry_count(not_a_plain_int, STAT_JOINT_ENTRIES) == STAT_JOINT_ENTRIES

    def test_a_status_without_the_count_attributes_means_the_full_lists(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from types import SimpleNamespace

        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        give_stat_full_length_arrays(
            mock_linuxcnc_stat, real_joint_count=3, real_spindle_count=1
        )
        status_without_counts = SimpleNamespace(
            joint=mock_linuxcnc_stat.joint,
            spindle=mock_linuxcnc_stat.spindle,
            homed=mock_linuxcnc_stat.homed,
            misc_error=(),
        )
        mapper = LinuxCNCMapper(status_without_counts)

        assert len(mapper._real_joints()) == STAT_JOINT_ENTRIES
        assert len(mapper._real_spindles()) == STAT_SPINDLE_ENTRIES
        assert len(mapper._map_limit_status().homed) == STAT_JOINT_ENTRIES


def tool_table_slot(tool_number, z_offset=0.0):
    """One entry of LinuxCNC's tool table; tool number 0 is an empty slot."""
    slot = MagicMock()
    slot.id = tool_number
    slot.xoffset = slot.yoffset = 0.0
    slot.zoffset = z_offset
    slot.aoffset = slot.boffset = slot.coffset = 0.0
    slot.uoffset = slot.voffset = slot.woffset = 0.0
    slot.diameter = 0.0
    slot.frontangle = slot.backangle = 0.0
    slot.orientation = 0
    return slot


class TestToolTableIndex:
    """Each returned tool carries its index in LinuxCNC's table, which pocket_prepped refers to."""

    def test_entries_keep_their_index_when_empty_slots_are_left_out(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        # Nothing in the spindle (slot 0), tools 5 and 9 with an empty slot between.
        mock_linuxcnc_stat.tool_table = [
            tool_table_slot(0), tool_table_slot(5, z_offset=1.5), tool_table_slot(0),
            tool_table_slot(9, z_offset=2.5), tool_table_slot(0),
        ]
        mock_linuxcnc_stat.pocket_prepped = 3

        tool_status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto().tool

        assert [entry.id for entry in tool_status.tool_table] == [5, 9]
        assert [entry.table_index for entry in tool_status.tool_table] == [1, 3]
        assert tool_status.pocket_prepped == 3
        prepped_tools = [
            entry for entry in tool_status.tool_table
            if entry.HasField("table_index") and entry.table_index == tool_status.pocket_prepped
        ]
        assert [entry.id for entry in prepped_tools] == [9]
        assert prepped_tools[0].z_offset == 2.5

    def test_index_zero_is_set_and_not_just_the_default(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        tool_status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto().tool

        assert len(tool_status.tool_table) == 1
        assert tool_status.tool_table[0].HasField("table_index")
        assert tool_status.tool_table[0].table_index == 0

    def test_a_prepped_empty_slot_matches_no_entry(
        self, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_grpc.linuxcnc_mapper import LinuxCNCMapper

        mock_linuxcnc_stat.tool_table = [tool_table_slot(0), tool_table_slot(5)]
        mock_linuxcnc_stat.pocket_prepped = 0

        tool_status = LinuxCNCMapper(mock_linuxcnc_stat).map_to_proto().tool

        assert [entry.table_index for entry in tool_status.tool_table] == [1]
        assert all(
            entry.table_index != tool_status.pocket_prepped
            for entry in tool_status.tool_table
        )
