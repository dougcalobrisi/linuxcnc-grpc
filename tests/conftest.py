"""
Pytest fixtures for LinuxCNC gRPC server tests.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Add src to path for imports
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def mock_hal_pins():
    """Sample HAL pin data for testing."""
    return [
        {
            "NAME": "axis.x.pos-cmd",
            "TYPE": 2,  # HAL_FLOAT
            "DIRECTION": 32,  # HAL_OUT
            "VALUE": 123.456,
            "OWNER": 1,
        },
        {
            "NAME": "spindle.0.speed-out",
            "TYPE": 2,  # HAL_FLOAT
            "DIRECTION": 32,  # HAL_OUT
            "VALUE": 1000.0,
            "OWNER": 2,
        },
        {
            "NAME": "iocontrol.0.emc-enable-in",
            "TYPE": 1,  # HAL_BIT
            "DIRECTION": 16,  # HAL_IN
            "VALUE": True,
            "OWNER": 3,
        },
    ]


@pytest.fixture
def mock_hal_signals():
    """Sample HAL signal data for testing."""
    return [
        {
            "NAME": "x-pos-cmd",
            "TYPE": 2,  # HAL_FLOAT
            "VALUE": 123.456,
            "DRIVER": "axis.x.pos-cmd",
        },
        {
            "NAME": "spindle-speed",
            "TYPE": 2,  # HAL_FLOAT
            "VALUE": 1000.0,
            "DRIVER": "spindle.0.speed-out",
        },
    ]


@pytest.fixture
def mock_hal_params():
    """Sample HAL parameter data for testing."""
    return [
        {
            "NAME": "pid.x.Pgain",
            "DIRECTION": 192,  # HAL_RW
            "VALUE": 10.5,
            "OWNER": 4,
        },
        {
            "NAME": "stepgen.0.maxvel",
            "DIRECTION": 192,  # HAL_RW
            "VALUE": 500.0,
            "OWNER": 5,
        },
        {
            "NAME": "motion.servo.overruns",
            "DIRECTION": 64,  # HAL_RO
            "VALUE": 0,
            "OWNER": 6,
        },
    ]


@pytest.fixture
def mock_linuxcnc_module():
    """Mock linuxcnc module with required constants."""
    mock = MagicMock()

    # Version
    mock.version = "2.9"

    # Error class (must inherit from BaseException for try/except)
    mock.error = type("LinuxCNCError", (Exception,), {})

    # RCS status
    mock.RCS_DONE = 1
    mock.RCS_EXEC = 2
    mock.RCS_ERROR = 3

    # Task modes
    mock.MODE_MANUAL = 1
    mock.MODE_AUTO = 2
    mock.MODE_MDI = 3

    # Task states
    mock.STATE_ESTOP = 1
    mock.STATE_ESTOP_RESET = 2
    mock.STATE_ON = 3
    mock.STATE_OFF = 4

    # Exec states
    mock.EXEC_ERROR = 1
    mock.EXEC_DONE = 2
    mock.EXEC_WAITING_FOR_MOTION = 3
    mock.EXEC_WAITING_FOR_MOTION_QUEUE = 4
    mock.EXEC_WAITING_FOR_IO = 5
    mock.EXEC_WAITING_FOR_MOTION_AND_IO = 6
    mock.EXEC_WAITING_FOR_DELAY = 7
    mock.EXEC_WAITING_FOR_SYSTEM_CMD = 8
    mock.EXEC_WAITING_FOR_SPINDLE_ORIENTED = 9

    # Interp states
    mock.INTERP_IDLE = 1
    mock.INTERP_READING = 2
    mock.INTERP_PAUSED = 3
    mock.INTERP_WAITING = 4

    # Trajectory modes
    mock.TRAJ_MODE_FREE = 1
    mock.TRAJ_MODE_COORD = 2
    mock.TRAJ_MODE_TELEOP = 3

    # Kinematics types
    mock.KINEMATICS_IDENTITY = 1
    mock.KINEMATICS_FORWARD_ONLY = 2
    mock.KINEMATICS_INVERSE_ONLY = 3
    mock.KINEMATICS_BOTH = 4

    # Motion types
    mock.MOTION_TYPE_TRAVERSE = 1
    mock.MOTION_TYPE_FEED = 2
    mock.MOTION_TYPE_ARC = 3
    mock.MOTION_TYPE_TOOLCHANGE = 4
    mock.MOTION_TYPE_PROBING = 5
    mock.MOTION_TYPE_INDEXROTARY = 6

    # Joint types
    mock.LINEAR = 1
    mock.ANGULAR = 2

    return mock


@pytest.fixture
def mock_linuxcnc_stat(mock_linuxcnc_module):
    """Mock linuxcnc.stat() object with sample data."""
    stat = MagicMock()

    # Basic info
    stat.debug = 0
    stat.state = mock_linuxcnc_module.RCS_DONE
    stat.echo_serial_number = 1234

    # Task status
    stat.task_mode = mock_linuxcnc_module.MODE_MANUAL
    stat.task_state = mock_linuxcnc_module.STATE_ON
    stat.exec_state = mock_linuxcnc_module.EXEC_DONE
    stat.interp_state = mock_linuxcnc_module.INTERP_IDLE
    stat.call_level = 0
    stat.read_line = 0
    stat.motion_line = 0
    stat.current_line = 0
    stat.file = ""
    stat.command = ""
    stat.program_units = 1
    stat.interpreter_errcode = 0
    stat.optional_stop = False
    stat.block_delete = False
    stat.task_paused = 0
    stat.input_timeout = False
    stat.rotation_xy = 0.0
    stat.ini_filename = "/home/user/linuxcnc/configs/sim/axis.ini"
    stat.delay_left = 0.0
    stat.queued_mdi_commands = 0

    # Position (9-element tuples)
    stat.position = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.actual_position = (1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.probed_position = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.dtg = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.g5x_offset = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.g92_offset = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.tool_offset = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stat.g5x_index = 1

    # Trajectory
    stat.linear_units = 1.0
    stat.angular_units = 1.0
    stat.cycle_time = 0.001
    stat.joints = 3
    stat.spindles = 1
    stat.axis_mask = 7
    stat.motion_mode = mock_linuxcnc_module.TRAJ_MODE_FREE
    stat.enabled = True
    stat.inpos = True
    stat.queue = 0
    stat.active_queue = 0
    stat.queue_full = False
    stat.motion_id = 0
    stat.paused = False
    stat.feedrate = 1.0
    stat.rapidrate = 1.0
    stat.velocity = 100.0
    stat.acceleration = 500.0
    stat.max_velocity = 200.0
    stat.max_acceleration = 1000.0
    stat.probe_tripped = False
    stat.probing = False
    stat.probe_val = 0
    stat.kinematics_type = mock_linuxcnc_module.KINEMATICS_IDENTITY
    stat.motion_type = 0
    stat.distance_to_go = 0.0
    stat.current_vel = 0.0
    stat.feed_override_enabled = True
    stat.adaptive_feed_enabled = False
    stat.feed_hold_enabled = True
    stat.num_extrajoints = 0

    # Joints - list of dicts
    stat.joint = [
        {
            "jointType": mock_linuxcnc_module.LINEAR,
            "units": 1.0,
            "backlash": 0.0,
            "min_position_limit": -100.0,
            "max_position_limit": 100.0,
            "max_ferror": 0.05,
            "min_ferror": 0.01,
            "ferror_current": 0.0,
            "ferror_highmark": 0.0,
            "output": 0.0,
            "input": 1.0,
            "velocity": 0.0,
            "inpos": True,
            "homing": False,
            "homed": True,
            "fault": False,
            "enabled": True,
            "min_soft_limit": False,
            "max_soft_limit": False,
            "min_hard_limit": False,
            "max_hard_limit": False,
            "override_limits": False,
        },
        {
            "jointType": mock_linuxcnc_module.LINEAR,
            "units": 1.0,
            "backlash": 0.0,
            "min_position_limit": -100.0,
            "max_position_limit": 100.0,
            "max_ferror": 0.05,
            "min_ferror": 0.01,
            "ferror_current": 0.0,
            "ferror_highmark": 0.0,
            "output": 0.0,
            "input": 2.0,
            "velocity": 0.0,
            "inpos": True,
            "homing": False,
            "homed": True,
            "fault": False,
            "enabled": True,
            "min_soft_limit": False,
            "max_soft_limit": False,
            "min_hard_limit": False,
            "max_hard_limit": False,
            "override_limits": False,
        },
        {
            "jointType": mock_linuxcnc_module.LINEAR,
            "units": 1.0,
            "backlash": 0.0,
            "min_position_limit": -50.0,
            "max_position_limit": 50.0,
            "max_ferror": 0.05,
            "min_ferror": 0.01,
            "ferror_current": 0.0,
            "ferror_highmark": 0.0,
            "output": 0.0,
            "input": 3.0,
            "velocity": 0.0,
            "inpos": True,
            "homing": False,
            "homed": True,
            "fault": False,
            "enabled": True,
            "min_soft_limit": False,
            "max_soft_limit": False,
            "min_hard_limit": False,
            "max_hard_limit": False,
            "override_limits": False,
        },
    ]

    # Axes
    stat.axis = [
        {"velocity": 0.0, "min_position_limit": -100.0, "max_position_limit": 100.0},
        {"velocity": 0.0, "min_position_limit": -100.0, "max_position_limit": 100.0},
        {"velocity": 0.0, "min_position_limit": -50.0, "max_position_limit": 50.0},
    ]

    # Spindles
    stat.spindle = [
        {
            "brake": False,
            "direction": 0,
            "enabled": False,
            "override_enabled": True,
            "speed": 0.0,
            "override": 1.0,
            "homed": False,
            "orient_state": 0,
            "orient_fault": 0,
        }
    ]

    # Tool
    mock_tool = MagicMock()
    mock_tool.id = 1
    mock_tool.xoffset = 0.0
    mock_tool.yoffset = 0.0
    mock_tool.zoffset = 1.5
    mock_tool.aoffset = 0.0
    mock_tool.boffset = 0.0
    mock_tool.coffset = 0.0
    mock_tool.uoffset = 0.0
    mock_tool.voffset = 0.0
    mock_tool.woffset = 0.0
    mock_tool.diameter = 6.0
    mock_tool.frontangle = 0.0
    mock_tool.backangle = 0.0
    mock_tool.orientation = 0
    stat.tool_table = [mock_tool]
    stat.pocket_prepped = 0
    stat.tool_in_spindle = 1
    stat.tool_from_pocket = 0

    # IO
    stat.estop = 0
    stat.mist = 0
    stat.flood = 0
    stat.ain = [0.0] * 16
    stat.aout = [0.0] * 16
    stat.din = [0] * 16
    stat.dout = [0] * 16

    # G-codes
    stat.gcodes = [100, 170, 400, 540, 800, 940, 980]
    stat.mcodes = [0, 5, 9]
    stat.settings = [0.0, 100.0, 1000.0, 0.0, 0.0]

    # Homed flags
    stat.homed = [1, 1, 1]

    return stat


@pytest.fixture
def mock_hal_module(mock_hal_pins, mock_hal_signals, mock_hal_params):
    """Mock the hal module for service tests."""
    mock = MagicMock()
    mock_component = MagicMock()
    mock.component.return_value = mock_component
    mock.get_info_pins.return_value = mock_hal_pins
    mock.get_info_signals.return_value = mock_hal_signals
    mock.get_info_params.return_value = mock_hal_params
    mock.component_exists.return_value = True
    mock.component_is_ready.return_value = True
    mock.pin_has_writer.return_value = True
    mock.get_value.return_value = 123.456
    return mock


@pytest.fixture
def mock_grpc_context():
    """Mock gRPC ServicerContext."""
    import grpc
    context = MagicMock(spec=grpc.ServicerContext)
    context.is_active.return_value = True
    return context


@pytest.fixture
def mock_linuxcnc_command():
    """Mock linuxcnc.command() object."""
    cmd = MagicMock()
    cmd.wait_complete.return_value = 1  # RCS_DONE
    return cmd


@pytest.fixture
def mock_linuxcnc_error_channel():
    """Mock linuxcnc.error_channel() object."""
    channel = MagicMock()
    channel.poll.return_value = None
    return channel
