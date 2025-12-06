#!/usr/bin/env python3
"""
Jog Axis Example

Demonstrates jogging an axis using the LinuxCNC gRPC server.
Supports both continuous jogging and incremental jogging.

Usage:
    python jog_axis.py [--host HOST] [--port PORT]

Safety Warning:
    This script moves the machine! Ensure you have clear access to E-stop
    and understand the jog parameters before running.
"""

import argparse
import sys
import time

import grpc

# Try installed package first, fall back to local src/ directory
try:
    from linuxcnc_grpc_server._generated import linuxcnc_pb2
    from linuxcnc_grpc_server._generated import linuxcnc_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from linuxcnc_grpc_server._generated import linuxcnc_pb2
    from linuxcnc_grpc_server._generated import linuxcnc_pb2_grpc


class LinuxCNCClient:
    """Simple client wrapper for LinuxCNC gRPC operations."""

    def __init__(self, host: str, port: int):
        self.channel = grpc.insecure_channel(f"{host}:{port}")
        self.stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(self.channel)
        self._serial = 0

    def close(self):
        self.channel.close()

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def get_status(self) -> linuxcnc_pb2.LinuxCNCStatus:
        """Get current machine status."""
        return self.stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

    def send_command(self, command: linuxcnc_pb2.LinuxCNCCommand) -> linuxcnc_pb2.CommandResponse:
        """Send a command and return the response."""
        command.serial = self._next_serial()
        command.timestamp = int(time.time() * 1e9)
        return self.stub.SendCommand(command)

    def set_mode(self, mode: linuxcnc_pb2.TaskMode) -> linuxcnc_pb2.CommandResponse:
        """Set task mode (MANUAL, AUTO, MDI)."""
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.mode.mode = mode
        return self.send_command(cmd)

    def set_state(self, state: linuxcnc_pb2.TaskState) -> linuxcnc_pb2.CommandResponse:
        """Set machine state (ESTOP, ESTOP_RESET, ON, OFF)."""
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.state.state = state
        return self.send_command(cmd)

    def jog_continuous(self, axis: int, velocity: float) -> linuxcnc_pb2.CommandResponse:
        """Start continuous jog on an axis. velocity > 0 for positive, < 0 for negative."""
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.jog.type = linuxcnc_pb2.JOG_CONTINUOUS
        cmd.jog.is_joint = False  # Jog in axis (teleop) mode
        cmd.jog.index = axis      # 0=X, 1=Y, 2=Z, etc.
        cmd.jog.velocity = velocity
        return self.send_command(cmd)

    def jog_increment(self, axis: int, velocity: float, increment: float) -> linuxcnc_pb2.CommandResponse:
        """Jog axis by a fixed increment."""
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.jog.type = linuxcnc_pb2.JOG_INCREMENT
        cmd.jog.is_joint = False
        cmd.jog.index = axis
        cmd.jog.velocity = abs(velocity)
        cmd.jog.increment = increment  # Signed increment
        return self.send_command(cmd)

    def jog_stop(self, axis: int) -> linuxcnc_pb2.CommandResponse:
        """Stop jogging on an axis."""
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.jog.type = linuxcnc_pb2.JOG_STOP
        cmd.jog.is_joint = False
        cmd.jog.index = axis
        cmd.jog.velocity = 0
        return self.send_command(cmd)


def ensure_machine_ready(client: LinuxCNCClient) -> bool:
    """Ensure machine is out of E-stop, powered on, and in manual mode."""
    status = client.get_status()

    # Check E-stop
    if status.task.task_state == linuxcnc_pb2.STATE_ESTOP:
        print("Machine is in E-stop. Resetting...")
        resp = client.set_state(linuxcnc_pb2.STATE_ESTOP_RESET)
        if resp.status != linuxcnc_pb2.RCS_DONE:
            print(f"Failed to reset E-stop: {resp.error_message}")
            return False
        time.sleep(0.1)

    # Power on
    status = client.get_status()
    if status.task.task_state != linuxcnc_pb2.STATE_ON:
        print("Powering on machine...")
        resp = client.set_state(linuxcnc_pb2.STATE_ON)
        if resp.status != linuxcnc_pb2.RCS_DONE:
            print(f"Failed to power on: {resp.error_message}")
            return False
        time.sleep(0.1)

    # Set manual mode for jogging
    status = client.get_status()
    if status.task.task_mode != linuxcnc_pb2.MODE_MANUAL:
        print("Setting manual mode...")
        resp = client.set_mode(linuxcnc_pb2.MODE_MANUAL)
        if resp.status != linuxcnc_pb2.RCS_DONE:
            print(f"Failed to set manual mode: {resp.error_message}")
            return False
        time.sleep(0.1)

    return True


def demo_incremental_jog(client: LinuxCNCClient):
    """Demonstrate incremental jogging."""
    print("\n--- Incremental Jog Demo ---")
    print("Jogging X axis +1.0 units...")

    # Jog X axis positive by 1.0 unit at 100 units/min
    resp = client.jog_increment(axis=0, velocity=100.0, increment=1.0)
    if resp.status != linuxcnc_pb2.RCS_DONE:
        print(f"Jog failed: {resp.error_message}")
        return

    # Wait for motion to complete
    time.sleep(1.0)

    # Show new position
    status = client.get_status()
    pos = status.position.actual_position
    print(f"New position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")


def demo_continuous_jog(client: LinuxCNCClient):
    """Demonstrate continuous jogging with stop."""
    print("\n--- Continuous Jog Demo ---")
    print("Jogging Y axis positive for 0.5 seconds...")

    # Start continuous jog on Y axis at 50 units/min
    resp = client.jog_continuous(axis=1, velocity=50.0)
    if resp.status != linuxcnc_pb2.RCS_DONE:
        print(f"Jog start failed: {resp.error_message}")
        return

    # Let it jog for a bit
    time.sleep(0.5)

    # Stop the jog
    print("Stopping jog...")
    resp = client.jog_stop(axis=1)
    if resp.status != linuxcnc_pb2.RCS_DONE:
        print(f"Jog stop failed: {resp.error_message}")
        return

    # Show new position
    status = client.get_status()
    pos = status.position.actual_position
    print(f"New position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")


def main():
    parser = argparse.ArgumentParser(description="Jog axis demo via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("--skip-demo", action="store_true",
                        help="Skip the demo movements (just show status)")
    args = parser.parse_args()

    client = LinuxCNCClient(args.host, args.port)

    try:
        # Show initial status
        status = client.get_status()
        pos = status.position.actual_position
        print(f"Current position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")

        if args.skip_demo:
            print("Skipping demo movements (--skip-demo)")
            return

        # Ensure machine is ready for jogging
        if not ensure_machine_ready(client):
            print("Could not prepare machine for jogging")
            sys.exit(1)

        # Run demos
        demo_incremental_jog(client)
        demo_continuous_jog(client)

        print("\nJog demo complete!")

    except grpc.RpcError as e:
        print(f"gRPC error: {e.code()}: {e.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
