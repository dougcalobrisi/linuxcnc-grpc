#!/usr/bin/env python3
"""
Get LinuxCNC Status Example

Connects to the gRPC server and displays the current machine status.
This is the simplest example - a good starting point for understanding the API.

Usage:
    python get_status.py [--host HOST] [--port PORT]
"""

import argparse
import sys


import grpc

# Try installed package first, fall back to local src/ directory
try:
    from linuxcnc_grpc._generated import linuxcnc_pb2
    from linuxcnc_grpc._generated import linuxcnc_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
    from linuxcnc_grpc._generated import linuxcnc_pb2
    from linuxcnc_grpc._generated import linuxcnc_pb2_grpc


def get_status(host: str, port: int) -> None:
    """Connect to LinuxCNC gRPC server and print status."""
    channel = grpc.insecure_channel(f"{host}:{port}")
    stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

    try:
        # Request current status
        request = linuxcnc_pb2.GetStatusRequest()
        status = stub.GetStatus(request)

        print("=" * 60)
        print("LinuxCNC Status")
        print("=" * 60)

        # Task status
        print(f"\n[Task]")
        print(f"  Mode:       {linuxcnc_pb2.TaskMode.Name(status.task.task_mode)}")
        print(f"  State:      {linuxcnc_pb2.TaskState.Name(status.task.task_state)}")
        print(f"  Exec State: {linuxcnc_pb2.ExecState.Name(status.task.exec_state)}")
        print(f"  Interp:     {linuxcnc_pb2.InterpState.Name(status.task.interp_state)}")
        if status.task.file:
            print(f"  File:       {status.task.file}")

        # Position
        pos = status.position.actual_position
        print(f"\n[Position]")
        print(f"  X: {pos.x:10.4f}  Y: {pos.y:10.4f}  Z: {pos.z:10.4f}")
        if pos.a or pos.b or pos.c:
            print(f"  A: {pos.a:10.4f}  B: {pos.b:10.4f}  C: {pos.c:10.4f}")

        # Trajectory
        print(f"\n[Trajectory]")
        print(f"  Enabled:    {status.trajectory.enabled}")
        print(f"  Feed Rate:  {status.trajectory.feedrate * 100:.1f}%")
        print(f"  Rapid Rate: {status.trajectory.rapidrate * 100:.1f}%")
        print(f"  Velocity:   {status.trajectory.current_vel:.2f}")

        # Joints
        print(f"\n[Joints]")
        for joint in status.joints:
            homed = "H" if joint.homed else "-"
            enabled = "E" if joint.enabled else "-"
            fault = "F" if joint.fault else "-"
            print(f"  Joint {joint.joint_number}: [{homed}{enabled}{fault}] pos={joint.input:10.4f}")

        # Spindles
        if status.spindles:
            print(f"\n[Spindles]")
            for spindle in status.spindles:
                direction = {-1: "REV", 0: "OFF", 1: "FWD"}.get(spindle.direction, "?")
                print(f"  Spindle {spindle.spindle_number}: {direction} @ {spindle.speed:.0f} RPM")

        # I/O
        print(f"\n[I/O]")
        print(f"  E-stop: {'ACTIVE' if status.io.estop else 'OK'}")
        print(f"  Mist:   {linuxcnc_pb2.CoolantState.Name(status.io.mist)}")
        print(f"  Flood:  {linuxcnc_pb2.CoolantState.Name(status.io.flood)}")

        # Active G-codes (formatted)
        if status.gcode.active_gcodes:
            gcodes = [f"G{g/10:.1f}" if g % 10 else f"G{g//10}"
                      for g in status.gcode.active_gcodes if g > 0]
            print(f"\n[Active G-codes]")
            print(f"  {' '.join(gcodes[:10])}")
            if len(gcodes) > 10:
                print(f"  {' '.join(gcodes[10:])}")

        # Errors
        if status.errors:
            print(f"\n[Errors]")
            for err in status.errors:
                print(f"  {linuxcnc_pb2.ErrorMessage.ErrorType.Name(err.type)}: {err.message}")

        print()

    except grpc.RpcError as e:
        print(f"gRPC error: {e.code()}: {e.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        channel.close()


def main():
    parser = argparse.ArgumentParser(description="Get LinuxCNC status via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    args = parser.parse_args()

    get_status(args.host, args.port)


if __name__ == "__main__":
    main()
