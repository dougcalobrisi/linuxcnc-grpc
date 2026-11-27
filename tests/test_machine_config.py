"""
Unit tests for the machine configuration builder and the GetMachineConfig RPC.
"""

import math
import sys
from unittest.mock import MagicMock, patch

import grpc
import pytest


# A three-axis mill, as in LinuxCNC's sim/axis sample configuration.
MILL_INI_VALUES = {
    ("EMC", "MACHINE"): "LinuxCNC-HAL-SIM-AXIS",
    ("KINS", "KINEMATICS"): "trivkins",
    ("KINS", "JOINTS"): "3",
    ("TRAJ", "COORDINATES"): "X Y Z",
    ("TRAJ", "MAX_LINEAR_VELOCITY"): "4",
    ("TRAJ", "DEFAULT_LINEAR_VELOCITY"): "1.2",
    ("DISPLAY", "MAX_FEED_OVERRIDE"): "1.2",
    ("DISPLAY", "MAX_SPINDLE_OVERRIDE"): "1.5",
    ("DISPLAY", "MIN_SPINDLE_OVERRIDE"): "0.5",
    ("AXIS_X", "MAX_VELOCITY"): "4",
    ("AXIS_X", "MAX_ACCELERATION"): "100.0",
    ("AXIS_Y", "MAX_VELOCITY"): "5",
    ("JOINT_0", "MAX_VELOCITY"): "5",
    ("JOINT_0", "MAX_ACCELERATION"): "150",
    ("JOINT_0", "HOME_SEQUENCE"): "1",
    ("JOINT_1", "HOME_SEQUENCE"): "0",
    ("JOINT_2", "HOME_SEQUENCE"): "-2",
}


def _lookup_from(ini_values):
    """An INI lookup backed by a dict, returning None for missing keys as linuxcnc.ini does."""
    return lambda section, key: ini_values.get((section, key))


@pytest.fixture
def machine_config_module(mock_linuxcnc_module):
    """The machine_config module, bound to the mocked linuxcnc module."""
    from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

    with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
        sys.modules.pop("linuxcnc_grpc.machine_config", None)
        from linuxcnc_grpc import machine_config

        yield machine_config
    sys.modules.pop("linuxcnc_grpc.machine_config", None)


def _build(machine_config_module, stat, ini_values, nc_files_directory="/nc"):
    from linuxcnc_pb import linuxcnc_pb2

    return machine_config_module.build_machine_config(
        stat,
        _lookup_from(ini_values) if ini_values is not None else None,
        nc_files_directory=nc_files_directory,
        kinematics_type=linuxcnc_pb2.KINEMATICS_IDENTITY,
    )


class TestJointAxisLetters:
    """Test the joint-to-axis mapping rule."""

    @pytest.mark.parametrize(
        "kinematics, joint_count, expected_letters",
        [
            ("trivkins", 3, ["X", "Y", "Z"]),
            ("trivkins", 9, list("XYZABCUVW")),
            ("trivkins coordinates=XZ", 2, ["X", "Z"]),
            ("trivkins coordinates=xz", 2, ["X", "Z"]),
            ("trivkins coordinates=XYYZ kinstype=BOTH", 4, ["X", "Y", "Y", "Z"]),
            ("trivkins kinstype=both coordinates=xyzy", 4, ["X", "Y", "Z", "Y"]),
            ("trivkins COORDINATES=XYZA", 4, ["X", "Y", "Z", "A"]),
            ("  trivkins   coordinates=XYZ ", 3, ["X", "Y", "Z"]),
            # More joints than letters: the joint past the letters drives no axis.
            ("trivkins coordinates=XZ", 3, ["X", "Z", ""]),
            # Fewer joints than letters: only the joints that exist are listed.
            ("trivkins coordinates=XYZ", 2, ["X", "Y"]),
        ],
    )
    def test_trivkins(self, machine_config_module, kinematics, joint_count, expected_letters):
        assert machine_config_module.joint_axis_letters(kinematics, joint_count) == expected_letters

    @pytest.mark.parametrize(
        "kinematics, joint_count, extra_joint_count, expected_letters",
        [
            # Default coordinates: the extra joint must not take the next letter, A.
            ("trivkins", 4, 1, ["X", "Y", "Z", ""]),
            ("trivkins coordinates=xyz", 5, 2, ["X", "Y", "Z", "", ""]),
            # LinuxCNC's 1extrajoint sample: a gantry plus one extra joint.
            ("trivkins coordinates=XYYZ kinstype=both", 5, 1, ["X", "Y", "Y", "Z", ""]),
            ("trivkins coordinates=XYYZ kinstype=both", 6, 2, ["X", "Y", "Y", "Z", "", ""]),
            # More letters than kinematics joints: the letters left over go to no joint.
            ("trivkins coordinates=XYZA", 4, 2, ["X", "Y", "", ""]),
            ("trivkins", 3, 3, ["", "", ""]),
            ("trivkins coordinates=XYZ", 3, 0, ["X", "Y", "Z"]),
        ],
    )
    def test_extra_joints_get_no_letter(
        self, machine_config_module, kinematics, joint_count, extra_joint_count, expected_letters
    ):
        """The last extra_joint_count joints are outside the kinematics."""
        letters = machine_config_module.joint_axis_letters(
            kinematics, joint_count, extra_joint_count
        )

        assert letters == expected_letters

    def test_extra_joints_do_not_make_other_kinematics_known(self, machine_config_module):
        assert machine_config_module.joint_axis_letters("genserkins", 6, 1) is None

    @pytest.mark.parametrize(
        "kinematics",
        [
            "",
            "genserkins",
            "scarakins coordinates=xyzcab",
            "xyzac-trt-kins sparm=identityfirst",
            "lineardeltakins",
            # Not the trivkins module, even though the name starts the same.
            "trivkinsx coordinates=XYZ",
            "trivkins coordinates=XYQ",
        ],
    )
    def test_unknown_for_other_kinematics(self, machine_config_module, kinematics):
        """Kinematics without a fixed joint-to-axis relation give no mapping."""
        assert machine_config_module.joint_axis_letters(kinematics, 3) is None


class TestBuildMachineConfig:
    """Test build_machine_config."""

    def test_three_axis_mill(self, machine_config_module, mock_linuxcnc_stat):
        from linuxcnc_pb import linuxcnc_pb2

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.ini_filename == mock_linuxcnc_stat.ini_filename
        assert config.ini_readable is True
        assert config.machine_name == "LinuxCNC-HAL-SIM-AXIS"
        assert config.kinematics == "trivkins"
        assert config.kinematics_module == "trivkins"
        assert config.kinematics_type == linuxcnc_pb2.KINEMATICS_IDENTITY
        assert config.coordinates == "XYZ"
        assert config.axis_mask == 7
        assert config.joint_count == 3
        assert config.spindle_count == 1
        assert config.joint_axis_mapping_known is True
        assert config.max_linear_velocity == 4.0
        assert config.default_linear_velocity == 1.2
        assert config.max_angular_velocity == 0.0
        assert config.max_feed_override == 1.2
        assert config.min_spindle_override == 0.5
        assert config.max_spindle_override == 1.5
        assert config.lathe is False
        assert config.nc_files_directory == "/nc"

        assert [joint.joint_number for joint in config.joints] == [0, 1, 2]
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z"]
        first_joint = config.joints[0]
        assert first_joint.joint_type == linuxcnc_pb2.JOINT_LINEAR
        assert first_joint.min_limit == -100.0
        assert first_joint.max_limit == 100.0
        assert first_joint.max_velocity == 5.0
        assert first_joint.max_acceleration == 150.0
        assert first_joint.units == 1.0

        assert [axis.letter for axis in config.axes] == ["X", "Y", "Z"]
        assert [axis.axis_number for axis in config.axes] == [0, 1, 2]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1], [2]]
        assert config.axes[0].max_velocity == 4.0
        assert config.axes[0].max_acceleration == 100.0
        assert config.axes[1].max_velocity == 5.0
        assert config.axes[2].max_velocity == 0.0

    def test_home_sequence_distinguishes_zero_from_unset(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """HOME_SEQUENCE 0 is a real value; a joint without the key leaves the field unset."""
        ini_values = dict(MILL_INI_VALUES)
        del ini_values[("JOINT_2", "HOME_SEQUENCE")]

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.joints[0].home_sequence == 1
        assert config.joints[1].HasField("home_sequence")
        assert config.joints[1].home_sequence == 0
        assert not config.joints[2].HasField("home_sequence")

    def test_negative_home_sequence(self, machine_config_module, mock_linuxcnc_stat):
        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.joints[2].home_sequence == -2

    def test_only_configured_joints_are_listed(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """stat.joint always has 16 entries; only stat.joints of them are real."""
        mock_linuxcnc_stat.joint = list(mock_linuxcnc_stat.joint) + [
            dict(mock_linuxcnc_stat.joint[0]) for _ in range(13)
        ]
        assert len(mock_linuxcnc_stat.joint) == 16

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.joint_count == 3
        assert len(config.joints) == 3

    def test_lathe_lists_only_axes_in_the_mask(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """An XZ lathe: joint 1 drives Z, and Y is not an axis of the machine."""
        mock_linuxcnc_stat.joints = 2
        mock_linuxcnc_stat.axis_mask = 0b101
        ini_values = {
            ("KINS", "KINEMATICS"): "trivkins coordinates=xz",
            ("TRAJ", "COORDINATES"): "X Z",
            ("DISPLAY", "LATHE"): "1",
            ("DISPLAY", "BACK_TOOL_LATHE"): "0",
        }

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.lathe is True
        assert config.back_tool_lathe is False
        assert config.coordinates == "XZ"
        assert [joint.axis_letter for joint in config.joints] == ["X", "Z"]
        assert [axis.letter for axis in config.axes] == ["X", "Z"]
        assert [axis.axis_number for axis in config.axes] == [0, 2]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1]]

    def test_gantry_maps_two_joints_to_one_axis(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        mock_linuxcnc_stat.joints = 4
        mock_linuxcnc_stat.joint = [dict(mock_linuxcnc_stat.joint[0]) for _ in range(4)]
        ini_values = {
            ("KINS", "KINEMATICS"): "trivkins coordinates=XYYZ kinstype=BOTH",
            ("TRAJ", "COORDINATES"): "XYYZ",
        }

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.kinematics_module == "trivkins"
        assert config.coordinates == "XYYZ"
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Y", "Z"]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1, 2], [3]]

    @staticmethod
    def _give_joints(stat, joint_count):
        """Make the machine have joint_count joints, each with its own lower limit."""
        stat.joints = joint_count
        stat.joint = [
            dict(stat.joint[0], min_position_limit=-100.0 - joint_number)
            for joint_number in range(joint_count)
        ]

    def test_extra_joint_is_listed_without_an_axis_letter(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """Default coordinates, four joints, one extra: joint 3 does not drive A."""
        self._give_joints(mock_linuxcnc_stat, 4)
        mock_linuxcnc_stat.num_extrajoints = 1
        mock_linuxcnc_stat.axis_mask = 0b1111
        mock_linuxcnc_stat.axis = [dict(mock_linuxcnc_stat.axis[0]) for _ in range(9)]
        ini_values = dict(MILL_INI_VALUES)
        ini_values[("JOINT_3", "MAX_VELOCITY")] = "7"
        ini_values[("JOINT_3", "HOME_SEQUENCE")] = "3"

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.joint_axis_mapping_known is True
        assert config.joint_count == 4
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z", ""]
        assert [axis.letter for axis in config.axes] == ["X", "Y", "Z", "A"]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1], [2], []]
        # The extra joint keeps the rest of its configuration.
        extra_joint = config.joints[3]
        assert extra_joint.joint_number == 3
        assert extra_joint.min_limit == -103.0
        assert extra_joint.max_velocity == 7.0
        assert extra_joint.home_sequence == 3

    def test_extra_joints_are_the_last_configured_joints_not_the_last_listed(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """stat.joint always has 16 entries; the extra joint is the last of stat.joints."""
        self._give_joints(mock_linuxcnc_stat, 16)
        mock_linuxcnc_stat.joints = 4
        mock_linuxcnc_stat.num_extrajoints = 1

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.joint_count == 4
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z", ""]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1], [2]]

    def test_gantry_with_extra_joints(self, machine_config_module, mock_linuxcnc_stat):
        """Repeated letters and extra joints together, as in LinuxCNC's extrajoints samples."""
        self._give_joints(mock_linuxcnc_stat, 6)
        mock_linuxcnc_stat.num_extrajoints = 2
        ini_values = {
            ("KINS", "KINEMATICS"): "trivkins coordinates=XYYZ kinstype=BOTH",
            ("TRAJ", "COORDINATES"): "XYYZ",
        }

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.joint_axis_mapping_known is True
        assert [joint.joint_number for joint in config.joints] == [0, 1, 2, 3, 4, 5]
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Y", "Z", "", ""]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1, 2], [3]]

    def test_extra_joint_does_not_take_a_coordinate_letter(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """With XYYZ and four joints of which one is extra, no joint drives Z."""
        self._give_joints(mock_linuxcnc_stat, 4)
        mock_linuxcnc_stat.num_extrajoints = 1
        ini_values = {("KINS", "KINEMATICS"): "trivkins coordinates=XYYZ kinstype=BOTH"}

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Y", ""]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1, 2], []]

    def test_no_extra_joints_maps_every_joint(self, machine_config_module, mock_linuxcnc_stat):
        self._give_joints(mock_linuxcnc_stat, 4)
        mock_linuxcnc_stat.num_extrajoints = 0

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z", "A"]

    def test_status_without_num_extrajoints_means_no_extra_joints(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """LinuxCNC 2.8's status has no num_extrajoints."""
        self._give_joints(mock_linuxcnc_stat, 4)
        # Deleting an attribute of a MagicMock makes reading it raise AttributeError.
        del mock_linuxcnc_stat.num_extrajoints

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.joint_axis_mapping_known is True
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z", "A"]

    @pytest.mark.parametrize(
        "unusable_count",
        [-1, 5, None, "1", 1.0, True],
        ids=["negative", "above-the-joint-count", "none", "text", "float", "bool"],
    )
    def test_unusable_num_extrajoints_means_no_extra_joints(
        self, machine_config_module, mock_linuxcnc_stat, unusable_count
    ):
        self._give_joints(mock_linuxcnc_stat, 4)
        mock_linuxcnc_stat.num_extrajoints = unusable_count

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert len(config.joints) == 4
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z", "A"]

    def test_every_joint_an_extra_joint(self, machine_config_module, mock_linuxcnc_stat):
        """num_extrajoints may equal the joint count: no joint drives an axis."""
        mock_linuxcnc_stat.num_extrajoints = 3

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.joint_axis_mapping_known is True
        assert [joint.axis_letter for joint in config.joints] == ["", "", ""]
        assert [list(axis.joints) for axis in config.axes] == [[], [], []]

    def test_extra_joints_leave_other_kinematics_unknown(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        self._give_joints(mock_linuxcnc_stat, 4)
        mock_linuxcnc_stat.num_extrajoints = 1
        ini_values = {("KINS", "KINEMATICS"): "genserkins"}

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.joint_axis_mapping_known is False
        assert [joint.axis_letter for joint in config.joints] == ["", "", "", ""]

    def test_non_trivial_kinematics_leaves_mapping_unknown(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        ini_values = {("KINS", "KINEMATICS"): "scarakins coordinates=xyzcab"}

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.joint_axis_mapping_known is False
        assert config.kinematics_module == "scarakins"
        assert [joint.axis_letter for joint in config.joints] == ["", "", ""]
        assert [list(axis.joints) for axis in config.axes] == [[], [], []]

    def test_unreadable_ini_still_reports_status_values(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        """Without the INI, status-based fields are filled and the mapping is unknown."""
        config = _build(machine_config_module, mock_linuxcnc_stat, None)

        assert config.ini_readable is False
        assert config.kinematics == ""
        assert config.joint_axis_mapping_known is False
        assert config.joint_count == 3
        assert config.joints[0].min_limit == -100.0
        assert config.joints[0].max_velocity == 0.0
        assert not config.joints[0].HasField("home_sequence")
        assert [axis.letter for axis in config.axes] == ["X", "Y", "Z"]
        assert config.linear_units_name == "mm"

    def test_non_numeric_ini_values_become_zero(
        self, machine_config_module, mock_linuxcnc_stat
    ):
        ini_values = dict(MILL_INI_VALUES)
        ini_values[("TRAJ", "MAX_LINEAR_VELOCITY")] = "fast"
        ini_values[("JOINT_0", "HOME_SEQUENCE")] = "first"

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.max_linear_velocity == 0.0
        assert not config.joints[0].HasField("home_sequence")

    @pytest.mark.parametrize("home_sequence_text", ["2147483648", "-2147483649", "9" * 30])
    def test_home_sequence_outside_int32_is_left_unset(
        self, machine_config_module, mock_linuxcnc_stat, caplog, home_sequence_text
    ):
        """home_sequence is an int32 field: a larger INI value must not fail the whole build."""
        ini_values = dict(MILL_INI_VALUES)
        ini_values[("JOINT_0", "HOME_SEQUENCE")] = home_sequence_text

        with caplog.at_level("WARNING", logger="linuxcnc_grpc.machine_config"):
            config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert not config.joints[0].HasField("home_sequence")
        assert config.joints[1].home_sequence == 0
        assert "[JOINT_0]HOME_SEQUENCE" in caplog.text

    @pytest.mark.parametrize("home_sequence", [2147483647, -2147483648])
    def test_home_sequence_at_the_int32_limits_is_kept(
        self, machine_config_module, mock_linuxcnc_stat, home_sequence
    ):
        ini_values = dict(MILL_INI_VALUES)
        ini_values[("JOINT_0", "HOME_SEQUENCE")] = str(home_sequence)

        config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.joints[0].home_sequence == home_sequence

    @pytest.mark.parametrize(
        "non_finite_text", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999", "-1e999"]
    )
    def test_non_finite_ini_values_become_zero(
        self, machine_config_module, mock_linuxcnc_stat, caplog, non_finite_text
    ):
        """float() accepts these, but none of them is a usable velocity or override."""
        ini_values = dict(MILL_INI_VALUES)
        ini_values[("TRAJ", "MAX_LINEAR_VELOCITY")] = non_finite_text
        ini_values[("JOINT_0", "MAX_VELOCITY")] = non_finite_text

        with caplog.at_level("WARNING", logger="linuxcnc_grpc.machine_config"):
            config = _build(machine_config_module, mock_linuxcnc_stat, ini_values)

        assert config.max_linear_velocity == 0.0
        assert config.joints[0].max_velocity == 0.0
        assert config.default_linear_velocity == 1.2
        assert "[TRAJ]MAX_LINEAR_VELOCITY" in caplog.text

    @pytest.mark.parametrize(
        "linear_units, expected_name",
        [(1.0, "mm"), (1.0 / 25.4, "inch"), (0.03937007874015748, "inch"), (0.1, "cm"), (0.5, "")],
    )
    def test_linear_unit_names(
        self, machine_config_module, mock_linuxcnc_stat, linear_units, expected_name
    ):
        mock_linuxcnc_stat.linear_units = linear_units

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.linear_units == linear_units
        assert config.linear_units_name == expected_name

    @pytest.mark.parametrize(
        "angular_units, expected_name",
        [(1.0, "degree"), (math.pi / 180.0, "radian"), (400.0 / 360.0, "grad"), (2.0, "")],
    )
    def test_angular_unit_names(
        self, machine_config_module, mock_linuxcnc_stat, angular_units, expected_name
    ):
        mock_linuxcnc_stat.angular_units = angular_units

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.angular_units_name == expected_name

    def test_angular_joint_type(
        self, machine_config_module, mock_linuxcnc_module, mock_linuxcnc_stat
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.joint[2]["jointType"] = mock_linuxcnc_module.ANGULAR

        config = _build(machine_config_module, mock_linuxcnc_stat, MILL_INI_VALUES)

        assert config.joints[2].joint_type == linuxcnc_pb2.JOINT_ANGULAR


class TestGetMachineConfigRpc:
    """Test the GetMachineConfig RPC wiring."""

    @pytest.fixture
    def make_service(self, mock_linuxcnc_module, mock_linuxcnc_stat):
        """Build servicers and stop their background threads afterwards."""
        services = []

        from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

        def _make_service(nc_files_dir):
            mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
            mock_linuxcnc_module.command.return_value = MagicMock()
            mock_linuxcnc_module.error_channel.return_value = MagicMock()
            with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
                sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
                sys.modules.pop("linuxcnc_grpc.machine_config", None)
                from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

                service = LinuxCNCServiceServicer(nc_files_dir=str(nc_files_dir))
            services.append(service)
            return service

        yield _make_service

    def test_reads_the_ini_file_named_in_status(
        self, make_service, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, tmp_path
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_module.ini.return_value.find.side_effect = _lookup_from(MILL_INI_VALUES)
        service = make_service(tmp_path)

        config = service.GetMachineConfig(
            linuxcnc_pb2.GetMachineConfigRequest(), mock_grpc_context
        )

        mock_linuxcnc_module.ini.assert_called_once_with(mock_linuxcnc_stat.ini_filename)
        assert config.ini_readable is True
        assert config.kinematics_type == linuxcnc_pb2.KINEMATICS_IDENTITY
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z"]
        assert config.nc_files_directory == str(tmp_path.resolve())
        mock_grpc_context.abort.assert_not_called()

    def test_unreadable_ini_is_not_an_error(
        self, make_service, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, tmp_path
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_module.ini.side_effect = mock_linuxcnc_module.error(
            "inifile.open(/x.ini) failed"
        )
        service = make_service(tmp_path)

        config = service.GetMachineConfig(
            linuxcnc_pb2.GetMachineConfigRequest(), mock_grpc_context
        )

        assert config.ini_readable is False
        assert config.joint_count == 3
        mock_grpc_context.abort.assert_not_called()

    def test_linuxcnc_error_is_unavailable(
        self, make_service, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, tmp_path
    ):
        from linuxcnc_pb import linuxcnc_pb2

        service = make_service(tmp_path)
        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("gone")

        service.GetMachineConfig(linuxcnc_pb2.GetMachineConfigRequest(), mock_grpc_context)

        mock_grpc_context.abort.assert_called_once()
        assert mock_grpc_context.abort.call_args[0][0] == grpc.StatusCode.UNAVAILABLE

    def test_nc_files_directory_that_is_a_symlink_loop_is_internal(
        self, make_service, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context, tmp_path
    ):
        """Python <= 3.12 reports a symlink loop from resolve() as RuntimeError."""
        from pathlib import Path

        from linuxcnc_pb import linuxcnc_pb2

        service = make_service(tmp_path)

        def resolve_reporting_a_loop(path, strict=False):
            raise RuntimeError(f"Symlink loop from {path}")

        with patch.object(Path, "resolve", resolve_reporting_a_loop):
            service.GetMachineConfig(linuxcnc_pb2.GetMachineConfigRequest(), mock_grpc_context)

        mock_grpc_context.abort.assert_called_once()
        assert mock_grpc_context.abort.call_args[0][0] == grpc.StatusCode.INTERNAL
