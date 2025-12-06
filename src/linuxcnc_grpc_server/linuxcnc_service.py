"""
LinuxCNCService gRPC implementation.

Provides real LinuxCNC machine control and status via gRPC.
Requires the linuxcnc Python module and a running LinuxCNC instance.
"""

import time
import logging
from typing import Iterator

import grpc
import linuxcnc
from ._generated import linuxcnc_pb2, linuxcnc_pb2_grpc
from .linuxcnc_mapper import LinuxCNCMapper

logger = logging.getLogger(__name__)


class LinuxCNCServiceServicer(linuxcnc_pb2_grpc.LinuxCNCServiceServicer):
    """
    gRPC servicer for LinuxCNC operations.

    Requires a running LinuxCNC instance to connect to.
    Raises RuntimeError if LinuxCNC is not available.
    """

    def __init__(self):
        """
        Initialize the service with real LinuxCNC connection.

        Raises:
            RuntimeError: If linuxcnc module cannot connect to LinuxCNC.
        """
        self._command_serial = 0
        self._stat = linuxcnc.stat()
        self._command = linuxcnc.command()
        self._error_channel = linuxcnc.error_channel()

        # Test the connection by polling
        self._stat.poll()
        logger.info("LinuxCNCService initialized (CONNECTED)")

    def _next_serial(self) -> int:
        """Get next command serial number."""
        self._command_serial += 1
        return self._command_serial

    # =========================================================================
    # GetStatus RPC
    # =========================================================================

    def GetStatus(
        self,
        request: linuxcnc_pb2.GetStatusRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.LinuxCNCStatus:
        """
        Get current LinuxCNC status.

        Polls the LinuxCNC stat channel and returns the current machine status.
        """
        logger.debug("GetStatus called")
        try:
            self._stat.poll()
            mapper = LinuxCNCMapper(self._stat)
            return mapper.map_to_proto()
        except linuxcnc.error as e:
            logger.error(f"LinuxCNC error in GetStatus: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"LinuxCNC error: {e}")
            return linuxcnc_pb2.LinuxCNCStatus()

    # =========================================================================
    # SendCommand RPC
    # =========================================================================

    def SendCommand(
        self,
        request: linuxcnc_pb2.LinuxCNCCommand,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.CommandResponse:
        """
        Send a command to LinuxCNC.

        Dispatches to the appropriate command handler based on the command type.
        """
        serial = request.serial or self._next_serial()
        command_type = request.WhichOneof("command")
        logger.debug(f"SendCommand: {command_type} (serial={serial})")

        try:
            handler = getattr(self, f"_handle_{command_type}_cmd", None)
            if handler:
                handler(request)
                return linuxcnc_pb2.CommandResponse(
                    serial=serial,
                    status=linuxcnc_pb2.RCS_DONE,
                    error_message=""
                )
            else:
                logger.error(f"No handler for command type: {command_type}")
                return linuxcnc_pb2.CommandResponse(
                    serial=serial,
                    status=linuxcnc_pb2.RCS_ERROR,
                    error_message=f"Unknown command type: {command_type}"
                )
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return linuxcnc_pb2.CommandResponse(
                serial=serial,
                status=linuxcnc_pb2.RCS_ERROR,
                error_message=str(e)
            )

    def _handle_state_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle state command (estop, on, off)."""
        state = request.state.state
        state_map = {
            linuxcnc_pb2.STATE_ESTOP: linuxcnc.STATE_ESTOP,
            linuxcnc_pb2.STATE_ESTOP_RESET: linuxcnc.STATE_ESTOP_RESET,
            linuxcnc_pb2.STATE_ON: linuxcnc.STATE_ON,
            linuxcnc_pb2.STATE_OFF: linuxcnc.STATE_OFF,
        }
        if state in state_map:
            self._command.state(state_map[state])
            logger.info(f"State command: {state}")
        else:
            raise ValueError(f"Unknown state: {state}")

    def _handle_mode_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle mode command (manual, auto, mdi)."""
        mode = request.mode.mode
        mode_map = {
            linuxcnc_pb2.MODE_MANUAL: linuxcnc.MODE_MANUAL,
            linuxcnc_pb2.MODE_AUTO: linuxcnc.MODE_AUTO,
            linuxcnc_pb2.MODE_MDI: linuxcnc.MODE_MDI,
        }
        if mode in mode_map:
            self._command.mode(mode_map[mode])
            logger.info(f"Mode command: {mode}")
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def _handle_mdi_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle MDI command (execute G-code string)."""
        command = request.mdi.command
        self._command.mdi(command)
        logger.info(f"MDI command: {command}")

    def _handle_jog_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle jog command (stop, continuous, increment)."""
        jog = request.jog
        if jog.type == linuxcnc_pb2.JOG_STOP:
            self._command.jog(linuxcnc.JOG_STOP, jog.is_joint, jog.index)
        elif jog.type == linuxcnc_pb2.JOG_CONTINUOUS:
            self._command.jog(linuxcnc.JOG_CONTINUOUS, jog.is_joint, jog.index, jog.velocity)
        elif jog.type == linuxcnc_pb2.JOG_INCREMENT:
            self._command.jog(linuxcnc.JOG_INCREMENT, jog.is_joint, jog.index, jog.velocity, jog.increment)
        else:
            raise ValueError(f"Unknown jog type: {jog.type}")
        logger.info(f"Jog command: type={jog.type}, is_joint={jog.is_joint}, index={jog.index}")

    def _handle_home_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle home command (-1 for all joints)."""
        joint = request.home.joint
        self._command.home(joint)
        logger.info(f"Home command: joint={joint}")

    def _handle_unhome_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle unhome command."""
        joint = request.unhome.joint
        self._command.unhome(joint)
        logger.info(f"Unhome command: joint={joint}")

    def _handle_spindle_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle spindle command (forward, reverse, off, increase, decrease)."""
        sp = request.spindle
        if sp.command == linuxcnc_pb2.SPINDLE_CMD_FORWARD:
            self._command.spindle(linuxcnc.SPINDLE_FORWARD, sp.speed, sp.spindle, sp.wait_for_at_speed)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_REVERSE:
            self._command.spindle(linuxcnc.SPINDLE_REVERSE, sp.speed, sp.spindle, sp.wait_for_at_speed)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_OFF:
            self._command.spindle(linuxcnc.SPINDLE_OFF, 0, sp.spindle)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_INCREASE:
            self._command.spindle(linuxcnc.SPINDLE_INCREASE, 0, sp.spindle)
        elif sp.command == linuxcnc_pb2.SPINDLE_CMD_DECREASE:
            self._command.spindle(linuxcnc.SPINDLE_DECREASE, 0, sp.spindle)
        else:
            raise ValueError(f"Unknown spindle command: {sp.command}")
        logger.info(f"Spindle command: cmd={sp.command}, speed={sp.speed}, spindle={sp.spindle}")

    def _handle_spindle_override_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle spindle override command."""
        so = request.spindle_override
        self._command.spindleoverride(so.scale, so.spindle)
        logger.info(f"Spindle override: scale={so.scale}, spindle={so.spindle}")

    def _handle_brake_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle brake command (engage/release)."""
        brake = request.brake
        if brake.state == linuxcnc_pb2.BRAKE_ENGAGE:
            self._command.brake(linuxcnc.BRAKE_ENGAGE, brake.spindle)
        else:
            self._command.brake(linuxcnc.BRAKE_RELEASE, brake.spindle)
        logger.info(f"Brake command: state={brake.state}, spindle={brake.spindle}")

    def _handle_feedrate_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle feedrate override command."""
        scale = request.feedrate.scale
        self._command.feedrate(scale)
        logger.info(f"Feedrate command: scale={scale}")

    def _handle_rapidrate_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle rapid rate override command."""
        scale = request.rapidrate.scale
        self._command.rapidrate(scale)
        logger.info(f"Rapidrate command: scale={scale}")

    def _handle_maxvel_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle max velocity command."""
        velocity = request.maxvel.velocity
        self._command.maxvel(velocity)
        logger.info(f"Maxvel command: velocity={velocity}")

    def _handle_coolant_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle coolant command (mist and flood)."""
        c = request.coolant
        if c.mist:
            self._command.mist(linuxcnc.MIST_ON)
        else:
            self._command.mist(linuxcnc.MIST_OFF)
        if c.flood:
            self._command.flood(linuxcnc.FLOOD_ON)
        else:
            self._command.flood(linuxcnc.FLOOD_OFF)
        logger.info(f"Coolant command: mist={c.mist}, flood={c.flood}")

    def _handle_tool_offset_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle tool offset command."""
        to = request.tool_offset
        self._command.tool_offset(
            to.tool_number, to.z_offset, to.x_offset,
            to.diameter, to.front_angle, to.back_angle, to.orientation
        )
        logger.info(f"Tool offset command: tool={to.tool_number}")

    def _handle_program_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle program command (open, run, pause, resume, step, abort)."""
        p = request.program
        cmd_type = p.WhichOneof("command")
        if cmd_type == "open":
            self._command.program_open(p.open)
        elif cmd_type == "run_from_line":
            self._command.auto(linuxcnc.AUTO_RUN, p.run_from_line)
        elif cmd_type == "pause" and p.pause:
            self._command.auto(linuxcnc.AUTO_PAUSE)
        elif cmd_type == "resume" and p.resume:
            self._command.auto(linuxcnc.AUTO_RESUME)
        elif cmd_type == "step" and p.step:
            self._command.auto(linuxcnc.AUTO_STEP)
        elif cmd_type == "abort" and p.abort:
            self._command.abort()
        else:
            raise ValueError(f"Unknown program command: {cmd_type}")
        logger.info(f"Program command: type={cmd_type}")

    def _handle_digital_output_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle digital output command."""
        do = request.digital_output
        self._command.set_digital_output(do.index, do.value)
        logger.info(f"Digital output command: index={do.index}, value={do.value}")

    def _handle_analog_output_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle analog output command."""
        ao = request.analog_output
        self._command.set_analog_output(ao.index, ao.value)
        logger.info(f"Analog output command: index={ao.index}, value={ao.value}")

    def _handle_set_limit_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle set limit command."""
        sl = request.set_limit
        self._command.set_min_limit(sl.joint, sl.min_limit)
        self._command.set_max_limit(sl.joint, sl.max_limit)
        logger.info(f"Set limit command: joint={sl.joint}, min={sl.min_limit}, max={sl.max_limit}")

    def _handle_override_config_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle override configuration command."""
        oc = request.override_config
        self._command.set_feed_override(oc.feed_override_enable)
        self._command.set_spindle_override(oc.spindle_override_enable, oc.spindle)
        self._command.set_feed_hold(oc.feed_hold_enable)
        self._command.set_adaptive_feed(oc.adaptive_feed_enable)
        logger.info(f"Override config command: feed_enable={oc.feed_override_enable}, spindle_enable={oc.spindle_override_enable}")

    def _handle_program_options_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle program options command (optional stop, block delete)."""
        po = request.program_options
        self._command.set_optional_stop(po.optional_stop)
        self._command.set_block_delete(po.block_delete)
        logger.info(f"Program options command: optional_stop={po.optional_stop}, block_delete={po.block_delete}")

    def _handle_teleop_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle teleop command."""
        enable = request.teleop.enable
        self._command.teleop_enable(enable)
        logger.info(f"Teleop command: enable={enable}")

    def _handle_traj_mode_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle trajectory mode command (free, coord, teleop)."""
        mode = request.traj_mode.mode
        mode_map = {
            linuxcnc_pb2.TRAJ_MODE_FREE: linuxcnc.TRAJ_MODE_FREE,
            linuxcnc_pb2.TRAJ_MODE_COORD: linuxcnc.TRAJ_MODE_COORD,
            linuxcnc_pb2.TRAJ_MODE_TELEOP: linuxcnc.TRAJ_MODE_TELEOP,
        }
        if mode in mode_map:
            self._command.traj_mode(mode_map[mode])
            logger.info(f"Traj mode command: mode={mode}")
        else:
            raise ValueError(f"Unknown traj mode: {mode}")

    def _handle_override_limits_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle override limits command (for recovery from limit switches)."""
        self._command.override_limits()
        logger.info("Override limits command")

    def _handle_reset_interpreter_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle reset interpreter command."""
        self._command.reset_interpreter()
        logger.info("Reset interpreter command")

    def _handle_load_tool_table_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle load tool table command (reloads from INI file)."""
        self._command.load_tool_table()
        logger.info("Load tool table command")

    def _handle_task_plan_sync_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle task plan sync command."""
        self._command.task_plan_synch()
        logger.info("Task plan sync command")

    def _handle_debug_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle debug command."""
        level = request.debug.level
        self._command.debug(level)
        logger.info(f"Debug command: level={level}")

    def _handle_operator_message_cmd(self, request: linuxcnc_pb2.LinuxCNCCommand) -> None:
        """Handle operator message command (error, text, display)."""
        om = request.operator_message
        if om.type == linuxcnc_pb2.OperatorMessageCommand.ERROR:
            self._command.error_msg(om.message)
        elif om.type == linuxcnc_pb2.OperatorMessageCommand.TEXT:
            self._command.text_msg(om.message)
        elif om.type == linuxcnc_pb2.OperatorMessageCommand.DISPLAY:
            self._command.display_msg(om.message)
        else:
            raise ValueError(f"Unknown operator message type: {om.type}")
        logger.info(f"Operator message command: type={om.type}, msg={om.message}")

    # =========================================================================
    # WaitComplete RPC
    # =========================================================================

    def WaitComplete(
        self,
        request: linuxcnc_pb2.WaitCompleteRequest,
        context: grpc.ServicerContext
    ) -> linuxcnc_pb2.CommandResponse:
        """
        Wait for command completion.

        Calls linuxcnc.command().wait_complete() and returns the result status.
        """
        timeout = request.timeout if request.timeout > 0 else 5.0
        logger.debug(f"WaitComplete called - serial={request.serial}, timeout={timeout}")

        try:
            result = self._command.wait_complete(timeout)

            if result == linuxcnc.RCS_DONE:
                status = linuxcnc_pb2.RCS_DONE
                error_message = ""
            elif result == linuxcnc.RCS_ERROR:
                status = linuxcnc_pb2.RCS_ERROR
                error_message = "Command failed"
            else:  # RCS_EXEC - still executing
                status = linuxcnc_pb2.RCS_EXEC
                error_message = ""

            return linuxcnc_pb2.CommandResponse(
                serial=request.serial,
                status=status,
                error_message=error_message
            )
        except linuxcnc.error as e:
            logger.error(f"LinuxCNC error in WaitComplete: {e}")
            return linuxcnc_pb2.CommandResponse(
                serial=request.serial,
                status=linuxcnc_pb2.RCS_ERROR,
                error_message=str(e)
            )

    # =========================================================================
    # StreamStatus RPC
    # =========================================================================

    def StreamStatus(
        self,
        request: linuxcnc_pb2.StreamStatusRequest,
        context: grpc.ServicerContext
    ) -> Iterator[linuxcnc_pb2.LinuxCNCStatus]:
        """
        Stream status updates.

        Polls LinuxCNC at the requested interval and streams status updates.
        """
        interval = request.interval if request.interval > 0 else 0.1
        logger.info(f"StreamStatus started - interval={interval}s")

        try:
            while context.is_active():
                self._stat.poll()
                mapper = LinuxCNCMapper(self._stat)
                yield mapper.map_to_proto()
                time.sleep(interval)
        except linuxcnc.error as e:
            logger.error(f"LinuxCNC error in StreamStatus: {e}")
        except Exception as e:
            logger.error(f"StreamStatus error: {e}")
        finally:
            logger.info("StreamStatus ended")

    # =========================================================================
    # StreamErrors RPC
    # =========================================================================

    def _map_error_type(self, linuxcnc_type: int) -> int:
        """Map linuxcnc error type constant to proto ErrorType enum."""
        mapping = {
            linuxcnc.OPERATOR_ERROR: linuxcnc_pb2.ErrorMessage.OPERATOR_ERROR,
            linuxcnc.OPERATOR_TEXT: linuxcnc_pb2.ErrorMessage.OPERATOR_TEXT,
            linuxcnc.OPERATOR_DISPLAY: linuxcnc_pb2.ErrorMessage.OPERATOR_DISPLAY,
            linuxcnc.NML_ERROR: linuxcnc_pb2.ErrorMessage.NML_ERROR,
            linuxcnc.NML_TEXT: linuxcnc_pb2.ErrorMessage.NML_TEXT,
            linuxcnc.NML_DISPLAY: linuxcnc_pb2.ErrorMessage.NML_DISPLAY,
        }
        return mapping.get(linuxcnc_type, linuxcnc_pb2.ErrorMessage.ERROR_TYPE_UNSPECIFIED)

    def StreamErrors(
        self,
        request: linuxcnc_pb2.StreamErrorsRequest,
        context: grpc.ServicerContext
    ) -> Iterator[linuxcnc_pb2.ErrorMessage]:
        """
        Stream error messages from LinuxCNC error channel.

        Polls the error channel and yields ErrorMessage for each error received.
        """
        logger.info("StreamErrors started")

        try:
            while context.is_active():
                error = self._error_channel.poll()
                if error:
                    error_type, message = error
                    yield linuxcnc_pb2.ErrorMessage(
                        type=self._map_error_type(error_type),
                        message=message,
                        timestamp=int(time.time() * 1e9)
                    )
                time.sleep(0.05)  # 50ms poll interval
        except Exception as e:
            logger.error(f"StreamErrors error: {e}")
        finally:
            logger.info("StreamErrors ended")
