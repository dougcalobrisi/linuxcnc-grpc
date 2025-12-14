#!/usr/bin/env python3
"""
Mock gRPC server for cross-language client testing.

This server provides deterministic responses for all LinuxCNC and HAL
gRPC methods, allowing integration tests to run without a real LinuxCNC
installation.

Usage:
    python tests/mock_server.py [--port PORT] [--host HOST]

The server prints "READY:<port>" to stdout when ready to accept connections.
"""

import argparse
import logging
import signal
import sys
import time
from concurrent import futures
from pathlib import Path

import grpc

# Add src to path for imports
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from linuxcnc_pb import (
    linuxcnc_pb2,
    linuxcnc_pb2_grpc,
    hal_pb2,
    hal_pb2_grpc,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Mock Data - Same as conftest.py but standalone
# =============================================================================

MOCK_HAL_PINS = [
    {
        "name": "axis.x.pos-cmd",
        "type": hal_pb2.HAL_FLOAT,
        "direction": hal_pb2.HAL_OUT,
        "value": 123.456,
        "component": "axis",
        "signal": "x-pos-cmd",
    },
    {
        "name": "spindle.0.speed-out",
        "type": hal_pb2.HAL_FLOAT,
        "direction": hal_pb2.HAL_OUT,
        "value": 1000.0,
        "component": "spindle",
        "signal": "spindle-speed",
    },
    {
        "name": "iocontrol.0.emc-enable-in",
        "type": hal_pb2.HAL_BIT,
        "direction": hal_pb2.HAL_IN,
        "value": True,
        "component": "iocontrol",
        "signal": "",
    },
]

MOCK_HAL_SIGNALS = [
    {
        "name": "x-pos-cmd",
        "type": hal_pb2.HAL_FLOAT,
        "value": 123.456,
        "driver": "axis.x.pos-cmd",
        "readers": ["joint.0.motor-pos-cmd"],
    },
    {
        "name": "spindle-speed",
        "type": hal_pb2.HAL_FLOAT,
        "value": 1000.0,
        "driver": "spindle.0.speed-out",
        "readers": [],
    },
]

MOCK_HAL_PARAMS = [
    {
        "name": "pid.x.Pgain",
        "type": hal_pb2.HAL_FLOAT,
        "direction": hal_pb2.HAL_RW,
        "value": 10.5,
        "component": "pid",
    },
    {
        "name": "stepgen.0.maxvel",
        "type": hal_pb2.HAL_FLOAT,
        "direction": hal_pb2.HAL_RW,
        "value": 500.0,
        "component": "stepgen",
    },
    {
        "name": "motion.servo.overruns",
        "type": hal_pb2.HAL_S32,
        "direction": hal_pb2.HAL_RO,
        "value": 0,
        "component": "motion",
    },
]

MOCK_HAL_COMPONENTS = [
    {"name": "axis", "id": 1, "ready": True, "type": 1, "pid": 0},
    {"name": "spindle", "id": 2, "ready": True, "type": 1, "pid": 0},
    {"name": "iocontrol", "id": 3, "ready": True, "type": 2, "pid": 1234},
]


# =============================================================================
# Mock LinuxCNC Service
# =============================================================================

class MockLinuxCNCServicer(linuxcnc_pb2_grpc.LinuxCNCServiceServicer):
    """Mock LinuxCNC gRPC service returning deterministic responses."""

    def __init__(self):
        self._command_serial = 0

    def GetStatus(self, request, context):
        """Return mock LinuxCNC status."""
        return self._build_status()

    def SendCommand(self, request, context):
        """Accept any command and return success."""
        self._command_serial += 1
        serial = request.serial if request.serial else self._command_serial
        logger.info(f"Received command serial={serial}: {request.WhichOneof('command')}")
        return linuxcnc_pb2.CommandResponse(
            serial=serial,
            status=linuxcnc_pb2.RCS_DONE,
            error_message="",
        )

    def WaitComplete(self, request, context):
        """Return immediate completion."""
        return linuxcnc_pb2.CommandResponse(
            serial=request.serial,
            status=linuxcnc_pb2.RCS_DONE,
            error_message="",
        )

    def StreamStatus(self, request, context):
        """Stream status updates at requested interval."""
        interval_ms = request.interval_ms if request.interval_ms > 0 else 100
        interval = interval_ms / 1000.0
        while context.is_active():
            yield self._build_status()
            time.sleep(interval)

    def StreamErrors(self, request, context):
        """Stream errors (none in mock)."""
        # In mock, we don't generate errors, just keep connection alive
        while context.is_active():
            time.sleep(1.0)

    def _build_status(self):
        """Build a complete mock status response."""
        # Use milliseconds for timestamp (nanoseconds exceeds JavaScript's MAX_SAFE_INTEGER)
        status = linuxcnc_pb2.LinuxCNCStatus(
            timestamp=int(time.time() * 1000),
            version="2.9.0-mock",
            debug=0,
        )

        # Task status
        status.task.CopyFrom(linuxcnc_pb2.TaskStatus(
            echo_serial_number=1234,
            state=linuxcnc_pb2.RCS_DONE,
            task_mode=linuxcnc_pb2.MODE_MANUAL,
            task_state=linuxcnc_pb2.STATE_ON,
            exec_state=linuxcnc_pb2.EXEC_DONE,
            interp_state=linuxcnc_pb2.INTERP_IDLE,
            call_level=0,
            read_line=0,
            motion_line=0,
            current_line=0,
            file="",
            command="",
            program_units=1,
            interpreter_errcode=0,
            optional_stop=False,
            block_delete=False,
            task_paused=0,
            input_timeout=False,
            rotation_xy=0.0,
            ini_filename="/home/user/linuxcnc/configs/sim/axis.ini",
            delay_left=0.0,
            queued_mdi_commands=0,
        ))

        # Trajectory status
        status.trajectory.CopyFrom(linuxcnc_pb2.TrajectoryStatus(
            linear_units=1.0,
            angular_units=1.0,
            cycle_time=0.001,
            joints=3,
            spindles=1,
            axis_mask=7,
            motion_mode=linuxcnc_pb2.TRAJ_MODE_FREE,
            enabled=True,
            inpos=True,
            queue=0,
            active_queue=0,
            queue_full=False,
            motion_id=0,
            paused=False,
            feedrate=1.0,
            rapidrate=1.0,
            velocity=100.0,
            acceleration=500.0,
            max_velocity=200.0,
            max_acceleration=1000.0,
            probe_tripped=False,
            probing=False,
            probe_val=0,
            kinematics_type=linuxcnc_pb2.KINEMATICS_IDENTITY,
            motion_type=linuxcnc_pb2.MOTION_TYPE_NONE,
            distance_to_go=0.0,
            current_vel=0.0,
            feed_override_enabled=True,
            adaptive_feed_enabled=False,
            feed_hold_enabled=True,
            num_extrajoints=0,
        ))

        # Position status
        status.position.CopyFrom(linuxcnc_pb2.PositionStatus(
            position=linuxcnc_pb2.Position(x=0, y=0, z=0),
            actual_position=linuxcnc_pb2.Position(x=1.0, y=2.0, z=3.0),
            probed_position=linuxcnc_pb2.Position(),
            dtg=linuxcnc_pb2.Position(),
            g5x_offset=linuxcnc_pb2.Position(),
            g92_offset=linuxcnc_pb2.Position(),
            tool_offset=linuxcnc_pb2.Position(),
            g5x_index=1,
        ))

        # Joints
        for i in range(3):
            status.joints.append(linuxcnc_pb2.JointStatus(
                joint_number=i,
                joint_type=linuxcnc_pb2.JOINT_LINEAR,
                units=1.0,
                backlash=0.0,
                min_position_limit=-100.0 if i < 2 else -50.0,
                max_position_limit=100.0 if i < 2 else 50.0,
                max_ferror=0.05,
                min_ferror=0.01,
                ferror_current=0.0,
                ferror_highmark=0.0,
                output=0.0,
                input=float(i + 1),
                velocity=0.0,
                inpos=True,
                homing=False,
                homed=True,
                fault=False,
                enabled=True,
                min_soft_limit=False,
                max_soft_limit=False,
                min_hard_limit=False,
                max_hard_limit=False,
                override_limits=False,
            ))

        # Axes
        for i in range(3):
            status.axes.append(linuxcnc_pb2.AxisStatus(
                axis_number=i,
                velocity=0.0,
                min_position_limit=-100.0 if i < 2 else -50.0,
                max_position_limit=100.0 if i < 2 else 50.0,
            ))

        # Spindle
        status.spindles.append(linuxcnc_pb2.SpindleStatus(
            spindle_number=0,
            brake=False,
            direction=0,
            enabled=False,
            override_enabled=True,
            speed=0.0,
            override=1.0,
            homed=False,
            orient_state=0,
            orient_fault=0,
        ))

        # Tool
        status.tool.CopyFrom(linuxcnc_pb2.ToolStatus(
            pocket_prepped=0,
            tool_in_spindle=1,
            tool_from_pocket=0,
            tool_table=[linuxcnc_pb2.ToolEntry(
                id=1,
                z_offset=1.5,
                diameter=6.0,
            )],
        ))

        # IO
        status.io.CopyFrom(linuxcnc_pb2.IOStatus(
            estop=False,
            mist=linuxcnc_pb2.COOLANT_OFF,
            flood=linuxcnc_pb2.COOLANT_OFF,
            analog_inputs=[0.0] * 16,
            analog_outputs=[0.0] * 16,
            digital_inputs=[False] * 16,
            digital_outputs=[False] * 16,
        ))

        # G-codes
        status.gcode.CopyFrom(linuxcnc_pb2.GCodeStatus(
            active_gcodes=[100, 170, 400, 540, 800, 940, 980],
            active_mcodes=[0, 5, 9],
            sequence_number=0,
            feed_rate=100.0,
            spindle_speed=1000.0,
            g64_p_tolerance=0.0,
            g64_q_tolerance=0.0,
        ))

        # Limits
        status.limits.CopyFrom(linuxcnc_pb2.LimitStatus(
            limit_flags=[0, 0, 0],
            homed=[True, True, True],
            misc_error=[0, 0, 0],
        ))

        return status


# =============================================================================
# Mock HAL Service
# =============================================================================

class MockHalServicer(hal_pb2_grpc.HalServiceServicer):
    """Mock HAL gRPC service returning deterministic responses."""

    def GetSystemStatus(self, request, context):
        """Return mock HAL system status."""
        return self._build_system_status()

    def SendCommand(self, request, context):
        """Accept commands and return success."""
        logger.info(f"HAL command: {request.WhichOneof('command')}")
        return hal_pb2.HalCommandResponse(
            serial=request.serial,
            success=True,
            error="",
        )

    def GetValue(self, request, context):
        """Return mock value for pin/signal/param."""
        return hal_pb2.GetValueResponse(
            serial=0,
            success=True,
            error="",
            value=hal_pb2.HalValue(float_value=123.456),
            type=hal_pb2.HAL_FLOAT,
        )

    def QueryPins(self, request, context):
        """Return mock pins matching pattern."""
        pins = self._filter_by_pattern(MOCK_HAL_PINS, request.pattern)
        return hal_pb2.QueryPinsResponse(
            serial=0,
            success=True,
            error="",
            pins=[self._build_pin_info(p) for p in pins],
        )

    def QuerySignals(self, request, context):
        """Return mock signals matching pattern."""
        signals = self._filter_by_pattern(MOCK_HAL_SIGNALS, request.pattern)
        return hal_pb2.QuerySignalsResponse(
            serial=0,
            success=True,
            error="",
            signals=[self._build_signal_info(s) for s in signals],
        )

    def QueryParams(self, request, context):
        """Return mock params matching pattern."""
        params = self._filter_by_pattern(MOCK_HAL_PARAMS, request.pattern)
        return hal_pb2.QueryParamsResponse(
            serial=0,
            success=True,
            error="",
            params=[self._build_param_info(p) for p in params],
        )

    def QueryComponents(self, request, context):
        """Return mock components."""
        return hal_pb2.QueryComponentsResponse(
            serial=0,
            success=True,
            error="",
            components=[self._build_component_info(c) for c in MOCK_HAL_COMPONENTS],
        )

    def StreamStatus(self, request, context):
        """Stream HAL status updates."""
        interval_ms = request.interval_ms if request.interval_ms > 0 else 100
        interval = interval_ms / 1000.0
        while context.is_active():
            yield self._build_system_status()
            time.sleep(interval)

    def WatchValues(self, request, context):
        """Watch for value changes (mock: no changes)."""
        interval_ms = request.interval_ms if request.interval_ms > 0 else 100
        interval = interval_ms / 1000.0
        while context.is_active():
            # In mock, values don't change, so just yield empty batches
            yield hal_pb2.ValueChangeBatch(
                timestamp=int(time.time() * 1000),
                changes=[],
            )
            time.sleep(interval)

    def _filter_by_pattern(self, items, pattern):
        """Filter items by glob pattern."""
        import fnmatch
        if not pattern or pattern == "*":
            return items
        return [i for i in items if fnmatch.fnmatch(i["name"], pattern)]

    def _build_system_status(self):
        """Build complete HAL system status."""
        return hal_pb2.HalSystemStatus(
            timestamp=int(time.time() * 1000),
            pins=[self._build_pin_info(p) for p in MOCK_HAL_PINS],
            signals=[self._build_signal_info(s) for s in MOCK_HAL_SIGNALS],
            params=[self._build_param_info(p) for p in MOCK_HAL_PARAMS],
            components=[self._build_component_info(c) for c in MOCK_HAL_COMPONENTS],
            message_level=hal_pb2.MSG_INFO,
            is_sim=True,
            is_rt=False,
            is_userspace=True,
            kernel_version="mock",
        )

    def _build_pin_info(self, pin):
        """Build HalPinInfo from dict."""
        value = self._make_hal_value(pin["type"], pin["value"])
        return hal_pb2.HalPinInfo(
            name=pin["name"],
            short_name=pin["name"].split(".")[-1],
            component=pin["component"],
            type=pin["type"],
            direction=pin["direction"],
            value=value,
            signal=pin.get("signal", ""),
            has_writer=True,
        )

    def _build_signal_info(self, sig):
        """Build HalSignalInfo from dict."""
        value = self._make_hal_value(sig["type"], sig["value"])
        return hal_pb2.HalSignalInfo(
            name=sig["name"],
            type=sig["type"],
            value=value,
            driver=sig["driver"],
            readers=sig.get("readers", []),
            reader_count=len(sig.get("readers", [])),
        )

    def _build_param_info(self, param):
        """Build HalParamInfo from dict."""
        value = self._make_hal_value(param["type"], param["value"])
        return hal_pb2.HalParamInfo(
            name=param["name"],
            short_name=param["name"].split(".")[-1],
            component=param["component"],
            type=param["type"],
            direction=param["direction"],
            value=value,
        )

    def _build_component_info(self, comp):
        """Build HalComponentInfo from dict."""
        return hal_pb2.HalComponentInfo(
            name=comp["name"],
            id=comp["id"],
            ready=comp["ready"],
            type=comp["type"],
            pid=comp["pid"],
        )

    def _make_hal_value(self, hal_type, value):
        """Create HalValue of appropriate type."""
        if hal_type == hal_pb2.HAL_BIT:
            return hal_pb2.HalValue(bit_value=bool(value))
        elif hal_type == hal_pb2.HAL_FLOAT:
            return hal_pb2.HalValue(float_value=float(value))
        elif hal_type == hal_pb2.HAL_S32:
            return hal_pb2.HalValue(s32_value=int(value))
        elif hal_type == hal_pb2.HAL_U32:
            return hal_pb2.HalValue(u32_value=int(value))
        elif hal_type == hal_pb2.HAL_S64:
            return hal_pb2.HalValue(s64_value=int(value))
        elif hal_type == hal_pb2.HAL_U64:
            return hal_pb2.HalValue(u64_value=int(value))
        else:
            return hal_pb2.HalValue(float_value=float(value))


# =============================================================================
# Server Main
# =============================================================================

def create_server(host: str = "0.0.0.0", port: int = 50051, max_workers: int = 10):
    """Create and configure the mock gRPC server."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))

    # Register services
    linuxcnc_pb2_grpc.add_LinuxCNCServiceServicer_to_server(
        MockLinuxCNCServicer(), server
    )
    hal_pb2_grpc.add_HalServiceServicer_to_server(
        MockHalServicer(), server
    )

    # Bind to port
    address = f"{host}:{port}"
    server.add_insecure_port(address)

    return server, address


def main():
    parser = argparse.ArgumentParser(description="LinuxCNC Mock gRPC Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=50051, help="Port to listen on")
    args = parser.parse_args()

    server, address = create_server(args.host, args.port)

    # Handle shutdown gracefully
    stop_event = []

    def handle_signal(signum, frame):
        logger.info("Shutting down...")
        stop_event.append(True)
        server.stop(grace=5)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start server
    server.start()

    # Print ready signal for test runners
    print(f"READY:{args.port}", flush=True)
    logger.info(f"Mock server listening on {address}")

    # Wait for termination
    server.wait_for_termination()


if __name__ == "__main__":
    main()
