#!/usr/bin/env python3
"""
MDI Command Example

Execute G-code commands via MDI (Manual Data Input) mode.
This is useful for sending individual G-code commands without loading a file.

Usage:
    python mdi_command.py "G0 X10 Y10"
    python mdi_command.py --interactive

Safety Warning:
    MDI commands execute immediately on the machine. Understand what
    each command does before running it.
"""

import argparse
import sys
import time

import grpc

# Try installed package first, fall back to local packages/ directory
try:
    from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "packages" / "python"))
    from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc


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
        return self.stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

    def send_command(self, command: linuxcnc_pb2.LinuxCNCCommand) -> linuxcnc_pb2.CommandResponse:
        command.serial = self._next_serial()
        command.timestamp = int(time.time() * 1e9)
        return self.stub.SendCommand(command)

    def wait_complete(self, serial: int, timeout: float = 30.0) -> linuxcnc_pb2.CommandResponse:
        """Wait for a command to complete."""
        request = linuxcnc_pb2.WaitCompleteRequest()
        request.serial = serial
        request.timeout = timeout
        return self.stub.WaitComplete(request)

    def set_mode(self, mode: linuxcnc_pb2.TaskMode) -> linuxcnc_pb2.CommandResponse:
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.mode.mode = mode
        return self.send_command(cmd)

    def set_state(self, state: linuxcnc_pb2.TaskState) -> linuxcnc_pb2.CommandResponse:
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.state.state = state
        return self.send_command(cmd)

    def mdi(self, gcode: str) -> "tuple[linuxcnc_pb2.CommandResponse, int]":
        """Send an MDI command. Returns (response, serial)."""
        cmd = linuxcnc_pb2.LinuxCNCCommand()
        cmd.mdi.command = gcode
        resp = self.send_command(cmd)
        return resp, cmd.serial


def ensure_mdi_ready(client: LinuxCNCClient) -> bool:
    """Ensure machine is ready for MDI commands."""
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

    # Set MDI mode
    status = client.get_status()
    if status.task.task_mode != linuxcnc_pb2.MODE_MDI:
        print("Setting MDI mode...")
        resp = client.set_mode(linuxcnc_pb2.MODE_MDI)
        if resp.status != linuxcnc_pb2.RCS_DONE:
            print(f"Failed to set MDI mode: {resp.error_message}")
            return False
        time.sleep(0.1)

    return True


def execute_mdi(client: LinuxCNCClient, gcode: str, wait: bool = True) -> bool:
    """Execute an MDI command and optionally wait for completion."""
    print(f"Executing: {gcode}")

    resp, serial = client.mdi(gcode)
    if resp.status == linuxcnc_pb2.RCS_ERROR:
        print(f"  Error: {resp.error_message}")
        return False

    if wait:
        # Wait for the command to complete
        print("  Waiting for completion...")
        resp = client.wait_complete(serial, timeout=60.0)
        if resp.status == linuxcnc_pb2.RCS_ERROR:
            print(f"  Error during execution: {resp.error_message}")
            return False
        print("  Done.")

    return True


def interactive_mode(client: LinuxCNCClient):
    """Interactive MDI shell."""
    print("\nInteractive MDI Mode")
    print("Type G-code commands to execute. Type 'quit' or 'exit' to quit.")
    print("Type 'status' to show current position.\n")

    while True:
        try:
            cmd = input("MDI> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break

        if not cmd:
            continue

        if cmd.lower() in ("quit", "exit", "q"):
            break

        if cmd.lower() == "status":
            status = client.get_status()
            pos = status.position.actual_position
            print(f"Position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")
            continue

        if cmd.lower() == "help":
            print("Commands:")
            print("  <G-code>  - Execute G-code command")
            print("  status    - Show current position")
            print("  quit      - Exit interactive mode")
            continue

        # Ensure we're still in MDI mode
        status = client.get_status()
        if status.task.task_mode != linuxcnc_pb2.MODE_MDI:
            if not ensure_mdi_ready(client):
                print("Failed to re-enter MDI mode")
                continue

        execute_mdi(client, cmd)


def main():
    parser = argparse.ArgumentParser(description="Execute MDI commands via gRPC")
    parser.add_argument("command", nargs="?", help="G-code command to execute")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Enter interactive MDI mode")
    parser.add_argument("--no-wait", action="store_true",
                        help="Don't wait for command completion")
    args = parser.parse_args()

    if not args.command and not args.interactive:
        parser.print_help()
        print("\nExamples:")
        print('  python mdi_command.py "G0 X10 Y10"')
        print('  python mdi_command.py "G1 X20 F100"')
        print('  python mdi_command.py --interactive')
        sys.exit(1)

    client = LinuxCNCClient(args.host, args.port)

    try:
        # Ensure machine is ready for MDI
        if not ensure_mdi_ready(client):
            print("Could not prepare machine for MDI")
            sys.exit(1)

        if args.interactive:
            interactive_mode(client)
        else:
            success = execute_mdi(client, args.command, wait=not args.no_wait)
            if not success:
                sys.exit(1)

            # Show final position
            status = client.get_status()
            pos = status.position.actual_position
            print(f"Position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")

    except grpc.RpcError as e:
        print(f"gRPC error: {e.code()}: {e.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
