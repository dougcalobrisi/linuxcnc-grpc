"""
Machine configuration builder.

Combines live status from linuxcnc.stat() with values from the machine's INI
file into a MachineConfig protobuf message, so clients don't have to guess
which joint drives which axis, what the limits are or what units are in use.
"""

import logging
import math
from typing import Callable

import linuxcnc

from linuxcnc_pb import linuxcnc_pb2

from .linuxcnc_mapper import real_entry_count

logger = logging.getLogger(__name__)

# Axis letters in LinuxCNC's fixed order; the index is the axis number.
AXIS_LETTERS = "XYZABCUVW"

# trivkins assigns joints to these letters, in order, when the INI gives no
# coordinates= parameter.
TRIVKINS_DEFAULT_COORDINATES = AXIS_LETTERS

# linear_units is machine units per mm; angular_units is machine units per degree.
LINEAR_UNIT_NAMES = (("mm", 1.0), ("inch", 1.0 / 25.4), ("cm", 0.1))
ANGULAR_UNIT_NAMES = (("degree", 1.0), ("radian", math.pi / 180.0), ("grad", 400.0 / 360.0))

# Range of the int32 protobuf fields that INI integers are stored in.
INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1

# Looks up one INI value: (section, key) -> the raw string, or None if absent.
IniLookup = Callable[[str, str], "str | None"]


def _unit_name(units_per_base_unit: float, known_units) -> str:
    """Name the unit whose scale factor matches, or '' if none does."""
    for unit_name, scale_factor in known_units:
        if math.isclose(units_per_base_unit, scale_factor, rel_tol=1e-6):
            return unit_name
    return ""


def _ini_string(ini_lookup: IniLookup, section: str, key: str) -> str:
    """An INI value with surrounding whitespace removed, or '' if absent."""
    raw_value = ini_lookup(section, key)
    return raw_value.strip() if isinstance(raw_value, str) else ""


def _ini_float(ini_lookup: IniLookup, section: str, key: str) -> float:
    """An INI value as a float, or 0.0 if absent or not a finite number."""
    text = _ini_string(ini_lookup, section, key)
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        number = math.nan
    # float() also accepts "nan", "inf" and overflowing values like "1e999".
    if not math.isfinite(number):
        logger.warning(f"INI value [{section}]{key} = {text!r} is not a number")
        return 0.0
    return number


def _ini_int(ini_lookup: IniLookup, section: str, key: str) -> int | None:
    """An INI value as an int, or None if absent or not an integer that fits int32."""
    text = _ini_string(ini_lookup, section, key)
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        number = None
    # The value goes into an int32 protobuf field, which raises on anything larger.
    if number is None or not INT32_MIN <= number <= INT32_MAX:
        logger.warning(f"INI value [{section}]{key} = {text!r} is not an integer")
        return None
    return number


def _ini_flag(ini_lookup: IniLookup, section: str, key: str) -> bool:
    """True for an INI value LinuxCNC treats as on (1, true, yes)."""
    return _ini_string(ini_lookup, section, key).lower() in ("1", "true", "yes")


def extra_joint_count(stat, joint_count: int) -> int:
    """
    How many of the machine's last joints are extra joints.

    motmod's num_extrajoints= parameter takes the last joints out of the
    kinematics: they are homed like any joint but drive no axis. LinuxCNC 2.8
    has no stat.num_extrajoints, so a missing value means none, and so does
    one that cannot be right (not an int, negative, more than the joints).

    Args:
        stat: A polled linuxcnc.stat() object.
        joint_count: Number of joints the machine has.
    """
    reported_count = getattr(stat, "num_extrajoints", 0)
    count_is_plain_int = (
        isinstance(reported_count, int) and not isinstance(reported_count, bool)
    )
    if count_is_plain_int and 0 <= reported_count <= joint_count:
        return reported_count
    return 0


def joint_axis_letters(
    kinematics: str, joint_count: int, trailing_extra_joints: int = 0
) -> list[str] | None:
    """
    Work out which axis letter each joint drives.

    Only trivkins has a fixed relation: joint N drives the Nth letter of its
    coordinates= parameter (XYZABCUVW when the parameter is absent). A letter
    may repeat, as in the gantry "XYYZ" where joints 1 and 2 both drive Y.

    Only the first joint_count - trailing_extra_joints joints take part in the
    kinematics. The extra joints after them drive no axis and get no letter.

    Args:
        kinematics: The [KINS]KINEMATICS value, e.g. "trivkins coordinates=XZ".
        joint_count: Number of joints the machine has, extra joints included.
        trailing_extra_joints: How many of the last joints are extra joints.

    Returns:
        One letter per joint ('' for an extra joint and for a joint beyond
        the letters given), or None when the kinematics module has no fixed
        joint-to-axis relation or the value is empty.
    """
    kinematics_words = kinematics.split()
    if not kinematics_words or kinematics_words[0] != "trivkins":
        return None

    coordinate_letters = TRIVKINS_DEFAULT_COORDINATES
    for parameter in kinematics_words[1:]:
        parameter_name, separator, parameter_value = parameter.partition("=")
        if separator and parameter_name.lower() == "coordinates":
            coordinate_letters = parameter_value.upper()

    if any(letter not in AXIS_LETTERS for letter in coordinate_letters):
        logger.warning(f"Unrecognised trivkins coordinates in {kinematics!r}")
        return None

    # Letters are dealt out to the kinematics joints only, so an extra joint
    # never takes the letter that follows them in the coordinates.
    kinematics_joint_count = max(joint_count - trailing_extra_joints, 0)
    lettered_joint_count = min(kinematics_joint_count, len(coordinate_letters))
    return [
        coordinate_letters[joint_number] if joint_number < lettered_joint_count else ""
        for joint_number in range(joint_count)
    ]


def build_machine_config(
    stat,
    ini_lookup: IniLookup | None,
    nc_files_directory: str,
    kinematics_type: int,
) -> linuxcnc_pb2.MachineConfig:
    """
    Build a MachineConfig from polled status and the INI file.

    Args:
        stat: A polled linuxcnc.stat() object. The caller holds the lock.
        ini_lookup: Returns the raw INI string for (section, key) or None, or
            is None itself when the INI file could not be opened.
        nc_files_directory: The directory the server's file RPCs work in.
        kinematics_type: stat.kinematics_type already mapped to the proto enum.
    """
    ini_readable = ini_lookup is not None
    if ini_lookup is None:
        def ini_lookup(section: str, key: str) -> None:
            return None

    # The same rule as the status mapper, so the two never disagree about
    # which joints and spindles the machine has, whatever the counts hold.
    joint_count = real_entry_count(getattr(stat, "joints", None), len(stat.joint))
    spindle_count = real_entry_count(getattr(stat, "spindles", None), len(stat.spindle))
    kinematics = _ini_string(ini_lookup, "KINS", "KINEMATICS")
    kinematics_words = kinematics.split()
    letters_by_joint = joint_axis_letters(
        kinematics, joint_count, extra_joint_count(stat, joint_count)
    )

    joint_type_names = {
        linuxcnc.LINEAR: linuxcnc_pb2.JOINT_LINEAR,
        linuxcnc.ANGULAR: linuxcnc_pb2.JOINT_ANGULAR,
    }

    joints = []
    for joint_number in range(joint_count):
        joint_status = stat.joint[joint_number]
        ini_section = f"JOINT_{joint_number}"
        joint_config = linuxcnc_pb2.JointConfig(
            joint_number=joint_number,
            joint_type=joint_type_names.get(
                joint_status["jointType"], linuxcnc_pb2.JOINT_TYPE_UNSPECIFIED
            ),
            axis_letter=letters_by_joint[joint_number] if letters_by_joint else "",
            min_limit=joint_status["min_position_limit"],
            max_limit=joint_status["max_position_limit"],
            max_velocity=_ini_float(ini_lookup, ini_section, "MAX_VELOCITY"),
            max_acceleration=_ini_float(ini_lookup, ini_section, "MAX_ACCELERATION"),
            units=joint_status["units"],
        )
        home_sequence = _ini_int(ini_lookup, ini_section, "HOME_SEQUENCE")
        if home_sequence is not None:
            joint_config.home_sequence = home_sequence
        joints.append(joint_config)

    axes = []
    for axis_number, axis_letter in enumerate(AXIS_LETTERS):
        if not stat.axis_mask & (1 << axis_number) or axis_number >= len(stat.axis):
            continue
        axis_status = stat.axis[axis_number]
        ini_section = f"AXIS_{axis_letter}"
        axes.append(linuxcnc_pb2.AxisConfig(
            axis_number=axis_number,
            letter=axis_letter,
            min_limit=axis_status["min_position_limit"],
            max_limit=axis_status["max_position_limit"],
            max_velocity=_ini_float(ini_lookup, ini_section, "MAX_VELOCITY"),
            max_acceleration=_ini_float(ini_lookup, ini_section, "MAX_ACCELERATION"),
            joints=[
                joint_number
                for joint_number, joint_letter in enumerate(letters_by_joint or [])
                if joint_letter == axis_letter
            ],
        ))

    return linuxcnc_pb2.MachineConfig(
        # LinuxCNC 2.8's status has no ini_filename.
        ini_filename=getattr(stat, "ini_filename", ""),
        ini_readable=ini_readable,
        machine_name=_ini_string(ini_lookup, "EMC", "MACHINE"),
        kinematics=kinematics,
        kinematics_module=kinematics_words[0] if kinematics_words else "",
        kinematics_type=kinematics_type,
        coordinates="".join(_ini_string(ini_lookup, "TRAJ", "COORDINATES").split()).upper(),
        axis_mask=stat.axis_mask,
        joint_count=joint_count,
        spindle_count=spindle_count,
        joint_axis_mapping_known=letters_by_joint is not None,
        linear_units=stat.linear_units,
        angular_units=stat.angular_units,
        linear_units_name=_unit_name(stat.linear_units, LINEAR_UNIT_NAMES),
        angular_units_name=_unit_name(stat.angular_units, ANGULAR_UNIT_NAMES),
        max_linear_velocity=_ini_float(ini_lookup, "TRAJ", "MAX_LINEAR_VELOCITY"),
        default_linear_velocity=_ini_float(ini_lookup, "TRAJ", "DEFAULT_LINEAR_VELOCITY"),
        max_angular_velocity=_ini_float(ini_lookup, "TRAJ", "MAX_ANGULAR_VELOCITY"),
        default_angular_velocity=_ini_float(ini_lookup, "TRAJ", "DEFAULT_ANGULAR_VELOCITY"),
        lathe=_ini_flag(ini_lookup, "DISPLAY", "LATHE"),
        back_tool_lathe=_ini_flag(ini_lookup, "DISPLAY", "BACK_TOOL_LATHE"),
        max_feed_override=_ini_float(ini_lookup, "DISPLAY", "MAX_FEED_OVERRIDE"),
        min_spindle_override=_ini_float(ini_lookup, "DISPLAY", "MIN_SPINDLE_OVERRIDE"),
        max_spindle_override=_ini_float(ini_lookup, "DISPLAY", "MAX_SPINDLE_OVERRIDE"),
        nc_files_directory=nc_files_directory,
        joints=joints,
        axes=axes,
    )
