"""
LinuxCNC stat() to protobuf mapper.

Maps linuxcnc.stat() object attributes to LinuxCNCStatus protobuf messages.
"""

import time
import linuxcnc

from linuxcnc_pb import linuxcnc_pb2


class LinuxCNCMapper:
    """Maps linuxcnc.stat() to LinuxCNCStatus protobuf messages."""

    def __init__(self, stat: linuxcnc.stat, version: str = "unknown"):
        """
        Initialize mapper with a polled stat object.

        Args:
            stat: A linuxcnc.stat() object that has been polled
            version: LinuxCNC version string (from linuxcnc.version)
        """
        self._stat = stat
        self._version = version

    def map_to_proto(self) -> linuxcnc_pb2.LinuxCNCStatus:
        """Map the stat object to a LinuxCNCStatus protobuf message."""
        s = self._stat
        return linuxcnc_pb2.LinuxCNCStatus(
            timestamp=int(time.time() * 1e9),
            version=self._version,
            debug=s.debug,
            task=self._map_task_status(),
            trajectory=self._map_trajectory_status(),
            position=self._map_position_status(),
            joints=[self._map_joint_status(j, i) for i, j in enumerate(s.joint)],
            axes=[self._map_axis_status(a, i) for i, a in enumerate(s.axis)],
            spindles=[self._map_spindle_status(sp, i) for i, sp in enumerate(s.spindle)],
            tool=self._map_tool_status(),
            io=self._map_io_status(),
            gcode=self._map_gcode_status(),
            limits=self._map_limit_status(),
            errors=[]
        )

    # =========================================================================
    # Position mapping
    # =========================================================================

    def _map_position(self, pos_tuple: tuple) -> linuxcnc_pb2.Position:
        """Map a 9-element position tuple to Position proto."""
        return linuxcnc_pb2.Position(
            x=pos_tuple[0],
            y=pos_tuple[1],
            z=pos_tuple[2],
            a=pos_tuple[3],
            b=pos_tuple[4],
            c=pos_tuple[5],
            u=pos_tuple[6],
            v=pos_tuple[7],
            w=pos_tuple[8]
        )

    def _map_position_status(self) -> linuxcnc_pb2.PositionStatus:
        """Map position-related stat attributes to PositionStatus proto."""
        s = self._stat
        return linuxcnc_pb2.PositionStatus(
            position=self._map_position(s.position),
            actual_position=self._map_position(s.actual_position),
            probed_position=self._map_position(s.probed_position),
            dtg=self._map_position(s.dtg),
            g5x_offset=self._map_position(s.g5x_offset),
            g92_offset=self._map_position(s.g92_offset),
            tool_offset=self._map_position(s.tool_offset),
            g5x_index=s.g5x_index
        )

    # =========================================================================
    # Task status mapping
    # =========================================================================

    def _map_task_status(self) -> linuxcnc_pb2.TaskStatus:
        """Map task-related stat attributes to TaskStatus proto."""
        s = self._stat
        return linuxcnc_pb2.TaskStatus(
            echo_serial_number=s.echo_serial_number,
            state=self._map_rcs_status(s.state),
            task_mode=self._map_task_mode(s.task_mode),
            task_state=self._map_task_state(s.task_state),
            exec_state=self._map_exec_state(s.exec_state),
            interp_state=self._map_interp_state(s.interp_state),
            call_level=s.call_level,
            read_line=s.read_line,
            motion_line=s.motion_line,
            current_line=s.current_line,
            file=s.file,
            command=s.command,
            program_units=s.program_units,
            interpreter_errcode=s.interpreter_errcode,
            optional_stop=s.optional_stop,
            block_delete=s.block_delete,
            task_paused=s.task_paused,
            input_timeout=s.input_timeout,
            rotation_xy=s.rotation_xy,
            ini_filename=s.ini_filename,
            delay_left=s.delay_left,
            queued_mdi_commands=s.queued_mdi_commands
        )

    # =========================================================================
    # Trajectory status mapping
    # =========================================================================

    def _map_trajectory_status(self) -> linuxcnc_pb2.TrajectoryStatus:
        """Map trajectory-related stat attributes to TrajectoryStatus proto."""
        s = self._stat
        return linuxcnc_pb2.TrajectoryStatus(
            linear_units=s.linear_units,
            angular_units=s.angular_units,
            cycle_time=s.cycle_time,
            joints=s.joints,
            spindles=s.spindles,
            axis_mask=s.axis_mask,
            motion_mode=self._map_traj_mode(s.motion_mode),
            enabled=s.enabled,
            inpos=s.inpos,
            queue=s.queue,
            active_queue=s.active_queue,
            queue_full=s.queue_full,
            motion_id=s.motion_id,
            paused=s.paused,
            feedrate=s.feedrate,
            rapidrate=s.rapidrate,
            velocity=s.velocity,
            acceleration=s.acceleration,
            max_velocity=s.max_velocity,
            max_acceleration=s.max_acceleration,
            probe_tripped=s.probe_tripped,
            probing=s.probing,
            probe_val=s.probe_val,
            kinematics_type=self._map_kinematics_type(s.kinematics_type),
            motion_type=self._map_motion_type(s.motion_type),
            distance_to_go=s.distance_to_go,
            current_vel=s.current_vel,
            feed_override_enabled=s.feed_override_enabled,
            adaptive_feed_enabled=s.adaptive_feed_enabled,
            feed_hold_enabled=s.feed_hold_enabled,
            num_extrajoints=s.num_extrajoints
        )

    # =========================================================================
    # Joint status mapping
    # =========================================================================

    def _map_joint_status(self, joint: dict, index: int) -> linuxcnc_pb2.JointStatus:
        """Map a joint dict to JointStatus proto."""
        return linuxcnc_pb2.JointStatus(
            joint_number=index,
            joint_type=self._map_joint_type(joint['jointType']),
            units=joint['units'],
            backlash=joint['backlash'],
            min_position_limit=joint['min_position_limit'],
            max_position_limit=joint['max_position_limit'],
            max_ferror=joint['max_ferror'],
            min_ferror=joint['min_ferror'],
            ferror_current=joint['ferror_current'],
            ferror_highmark=joint['ferror_highmark'],
            output=joint['output'],
            input=joint['input'],
            velocity=joint['velocity'],
            inpos=joint['inpos'],
            homing=joint['homing'],
            homed=joint['homed'],
            fault=joint['fault'],
            enabled=joint['enabled'],
            min_soft_limit=joint['min_soft_limit'],
            max_soft_limit=joint['max_soft_limit'],
            min_hard_limit=joint['min_hard_limit'],
            max_hard_limit=joint['max_hard_limit'],
            override_limits=joint['override_limits']
        )

    # =========================================================================
    # Axis status mapping
    # =========================================================================

    def _map_axis_status(self, axis: dict, index: int) -> linuxcnc_pb2.AxisStatus:
        """Map an axis dict to AxisStatus proto."""
        return linuxcnc_pb2.AxisStatus(
            axis_number=index,
            velocity=axis['velocity'],
            min_position_limit=axis['min_position_limit'],
            max_position_limit=axis['max_position_limit']
        )

    # =========================================================================
    # Spindle status mapping
    # =========================================================================

    def _map_spindle_status(self, spindle: dict, index: int) -> linuxcnc_pb2.SpindleStatus:
        """Map a spindle dict to SpindleStatus proto."""
        return linuxcnc_pb2.SpindleStatus(
            spindle_number=index,
            brake=spindle['brake'],
            direction=spindle['direction'],
            enabled=spindle['enabled'],
            override_enabled=spindle['override_enabled'],
            speed=spindle['speed'],
            override=spindle['override'],
            homed=spindle['homed'],
            orient_state=spindle['orient_state'],
            orient_fault=spindle['orient_fault']
        )

    # =========================================================================
    # Tool status mapping
    # =========================================================================

    def _map_tool_entry(self, tool) -> linuxcnc_pb2.ToolEntry:
        """Map a tool table entry to ToolEntry proto."""
        return linuxcnc_pb2.ToolEntry(
            id=tool.id,
            x_offset=tool.xoffset,
            y_offset=tool.yoffset,
            z_offset=tool.zoffset,
            a_offset=tool.aoffset,
            b_offset=tool.boffset,
            c_offset=tool.coffset,
            u_offset=tool.uoffset,
            v_offset=tool.voffset,
            w_offset=tool.woffset,
            diameter=tool.diameter,
            front_angle=tool.frontangle,
            back_angle=tool.backangle,
            orientation=tool.orientation,
            comment=""
        )

    def _map_tool_status(self) -> linuxcnc_pb2.ToolStatus:
        """Map tool-related stat attributes to ToolStatus proto."""
        s = self._stat
        # Filter to only tools with non-zero IDs
        filtered_tools = [
            self._map_tool_entry(t)
            for t in s.tool_table
            if t.id != 0
        ]
        return linuxcnc_pb2.ToolStatus(
            pocket_prepped=s.pocket_prepped,
            tool_in_spindle=s.tool_in_spindle,
            tool_from_pocket=s.tool_from_pocket,
            tool_table=filtered_tools
        )

    # =========================================================================
    # IO status mapping
    # =========================================================================

    def _map_coolant_state(self, value: int) -> linuxcnc_pb2.CoolantState:
        """Map coolant value to CoolantState enum."""
        return linuxcnc_pb2.COOLANT_ON if value else linuxcnc_pb2.COOLANT_OFF

    def _map_io_status(self) -> linuxcnc_pb2.IOStatus:
        """Map I/O-related stat attributes to IOStatus proto."""
        s = self._stat
        return linuxcnc_pb2.IOStatus(
            estop=bool(s.estop),
            mist=self._map_coolant_state(s.mist),
            flood=self._map_coolant_state(s.flood),
            analog_inputs=list(s.ain),
            analog_outputs=list(s.aout),
            digital_inputs=[bool(v) for v in s.din],
            digital_outputs=[bool(v) for v in s.dout]
        )

    # =========================================================================
    # GCode status mapping
    # =========================================================================

    def _map_gcode_status(self) -> linuxcnc_pb2.GCodeStatus:
        """Map G-code-related stat attributes to GCodeStatus proto."""
        s = self._stat
        settings = s.settings
        return linuxcnc_pb2.GCodeStatus(
            active_gcodes=list(s.gcodes),
            active_mcodes=list(s.mcodes),
            sequence_number=settings[0] if len(settings) > 0 else 0.0,
            feed_rate=settings[1] if len(settings) > 1 else 0.0,
            spindle_speed=settings[2] if len(settings) > 2 else 0.0,
            g64_p_tolerance=settings[3] if len(settings) > 3 else 0.0,
            g64_q_tolerance=settings[4] if len(settings) > 4 else 0.0
        )

    # =========================================================================
    # Limit status mapping
    # =========================================================================

    def _map_limit_status(self) -> linuxcnc_pb2.LimitStatus:
        """Map limit-related stat attributes to LimitStatus proto."""
        s = self._stat
        limit_flags = []
        for joint in s.joint:
            flags = 0
            if joint['min_hard_limit']:
                flags |= 1
            if joint['max_hard_limit']:
                flags |= 2
            if joint['min_soft_limit']:
                flags |= 4
            if joint['max_soft_limit']:
                flags |= 8
            limit_flags.append(flags)

        return linuxcnc_pb2.LimitStatus(
            limit_flags=limit_flags,
            homed=list(s.homed),
            misc_error=[0] * len(s.joint)
        )

    # =========================================================================
    # Enum mapping helpers
    # =========================================================================

    def _map_rcs_status(self, value: int) -> linuxcnc_pb2.RcsStatus:
        """Map LinuxCNC RCS status to RcsStatus proto enum."""
        mapping = {
            linuxcnc.RCS_DONE: linuxcnc_pb2.RCS_DONE,
            linuxcnc.RCS_EXEC: linuxcnc_pb2.RCS_EXEC,
            linuxcnc.RCS_ERROR: linuxcnc_pb2.RCS_ERROR,
        }
        return mapping.get(value, linuxcnc_pb2.RCS_STATUS_UNSPECIFIED)

    def _map_task_mode(self, value: int) -> linuxcnc_pb2.TaskMode:
        """Map LinuxCNC task mode to TaskMode proto enum."""
        mapping = {
            linuxcnc.MODE_MANUAL: linuxcnc_pb2.MODE_MANUAL,
            linuxcnc.MODE_AUTO: linuxcnc_pb2.MODE_AUTO,
            linuxcnc.MODE_MDI: linuxcnc_pb2.MODE_MDI,
        }
        return mapping.get(value, linuxcnc_pb2.TASK_MODE_UNSPECIFIED)

    def _map_task_state(self, value: int) -> linuxcnc_pb2.TaskState:
        """Map LinuxCNC task state to TaskState proto enum."""
        mapping = {
            linuxcnc.STATE_ESTOP: linuxcnc_pb2.STATE_ESTOP,
            linuxcnc.STATE_ESTOP_RESET: linuxcnc_pb2.STATE_ESTOP_RESET,
            linuxcnc.STATE_ON: linuxcnc_pb2.STATE_ON,
            linuxcnc.STATE_OFF: linuxcnc_pb2.STATE_OFF,
        }
        return mapping.get(value, linuxcnc_pb2.TASK_STATE_UNSPECIFIED)

    def _map_exec_state(self, value: int) -> linuxcnc_pb2.ExecState:
        """Map LinuxCNC exec state to ExecState proto enum."""
        mapping = {
            linuxcnc.EXEC_ERROR: linuxcnc_pb2.EXEC_ERROR,
            linuxcnc.EXEC_DONE: linuxcnc_pb2.EXEC_DONE,
            linuxcnc.EXEC_WAITING_FOR_MOTION: linuxcnc_pb2.EXEC_WAITING_FOR_MOTION,
            linuxcnc.EXEC_WAITING_FOR_MOTION_QUEUE: linuxcnc_pb2.EXEC_WAITING_FOR_MOTION_QUEUE,
            linuxcnc.EXEC_WAITING_FOR_IO: linuxcnc_pb2.EXEC_WAITING_FOR_IO,
            linuxcnc.EXEC_WAITING_FOR_MOTION_AND_IO: linuxcnc_pb2.EXEC_WAITING_FOR_MOTION_AND_IO,
            linuxcnc.EXEC_WAITING_FOR_DELAY: linuxcnc_pb2.EXEC_WAITING_FOR_DELAY,
            linuxcnc.EXEC_WAITING_FOR_SYSTEM_CMD: linuxcnc_pb2.EXEC_WAITING_FOR_SYSTEM_CMD,
            linuxcnc.EXEC_WAITING_FOR_SPINDLE_ORIENTED: linuxcnc_pb2.EXEC_WAITING_FOR_SPINDLE_ORIENTED,
        }
        return mapping.get(value, linuxcnc_pb2.EXEC_STATE_UNSPECIFIED)

    def _map_interp_state(self, value: int) -> linuxcnc_pb2.InterpState:
        """Map LinuxCNC interp state to InterpState proto enum."""
        mapping = {
            linuxcnc.INTERP_IDLE: linuxcnc_pb2.INTERP_IDLE,
            linuxcnc.INTERP_READING: linuxcnc_pb2.INTERP_READING,
            linuxcnc.INTERP_PAUSED: linuxcnc_pb2.INTERP_PAUSED,
            linuxcnc.INTERP_WAITING: linuxcnc_pb2.INTERP_WAITING,
        }
        return mapping.get(value, linuxcnc_pb2.INTERP_STATE_UNSPECIFIED)

    def _map_traj_mode(self, value: int) -> linuxcnc_pb2.TrajMode:
        """Map LinuxCNC trajectory mode to TrajMode proto enum."""
        mapping = {
            linuxcnc.TRAJ_MODE_FREE: linuxcnc_pb2.TRAJ_MODE_FREE,
            linuxcnc.TRAJ_MODE_COORD: linuxcnc_pb2.TRAJ_MODE_COORD,
            linuxcnc.TRAJ_MODE_TELEOP: linuxcnc_pb2.TRAJ_MODE_TELEOP,
        }
        return mapping.get(value, linuxcnc_pb2.TRAJ_MODE_UNSPECIFIED)

    def _map_kinematics_type(self, value: int) -> linuxcnc_pb2.KinematicsType:
        """Map LinuxCNC kinematics type to KinematicsType proto enum."""
        mapping = {
            linuxcnc.KINEMATICS_IDENTITY: linuxcnc_pb2.KINEMATICS_IDENTITY,
            linuxcnc.KINEMATICS_FORWARD_ONLY: linuxcnc_pb2.KINEMATICS_FORWARD_ONLY,
            linuxcnc.KINEMATICS_INVERSE_ONLY: linuxcnc_pb2.KINEMATICS_INVERSE_ONLY,
            linuxcnc.KINEMATICS_BOTH: linuxcnc_pb2.KINEMATICS_BOTH,
        }
        return mapping.get(value, linuxcnc_pb2.KINEMATICS_UNSPECIFIED)

    def _map_motion_type(self, value: int) -> linuxcnc_pb2.MotionType:
        """Map LinuxCNC motion type to MotionType proto enum."""
        mapping = {
            0: linuxcnc_pb2.MOTION_TYPE_NONE,
            linuxcnc.MOTION_TYPE_TRAVERSE: linuxcnc_pb2.MOTION_TYPE_TRAVERSE,
            linuxcnc.MOTION_TYPE_FEED: linuxcnc_pb2.MOTION_TYPE_FEED,
            linuxcnc.MOTION_TYPE_ARC: linuxcnc_pb2.MOTION_TYPE_ARC,
            linuxcnc.MOTION_TYPE_TOOLCHANGE: linuxcnc_pb2.MOTION_TYPE_TOOLCHANGE,
            linuxcnc.MOTION_TYPE_PROBING: linuxcnc_pb2.MOTION_TYPE_PROBING,
            linuxcnc.MOTION_TYPE_INDEXROTARY: linuxcnc_pb2.MOTION_TYPE_INDEXROTARY,
        }
        return mapping.get(value, linuxcnc_pb2.MOTION_TYPE_NONE)

    def _map_joint_type(self, value: int) -> linuxcnc_pb2.JointType:
        """Map LinuxCNC joint type to JointType proto enum."""
        mapping = {
            linuxcnc.LINEAR: linuxcnc_pb2.JOINT_LINEAR,
            linuxcnc.ANGULAR: linuxcnc_pb2.JOINT_ANGULAR,
        }
        return mapping.get(value, linuxcnc_pb2.JOINT_TYPE_UNSPECIFIED)
