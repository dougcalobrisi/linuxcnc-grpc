#!/usr/bin/env python3
"""
MDI Command Example

Execute G-code commands via MDI (Manual Data Input) mode.
This is useful for sending individual G-code commands without loading a file.

Usage:
    python mdi_command.py "G0 X10 Y10"
    python mdi_command.py --interactive

Before it sends anything the example checks that the machine is on, that
every joint is homed and that the interpreter is idle. It does not take the
machine out of E-stop, power it on or home it: do that at the machine first.
It then switches to MDI mode. The checks are repeated before every command,
so in interactive mode a command is not sent while an earlier one is still
running.

Safety Warning:
    MDI commands execute immediately on the machine. Understand what
    each command does before running it. Ctrl+C stops this program, not the
    machine: a command that was already sent keeps running.
"""

import argparse
import math
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


# Deadlines, in seconds. Every RPC gets one.
RPC_TIMEOUT = 5.0             # GetStatus and SendCommand
STATE_CHANGE_TIMEOUT = 5.0    # A mode change must finish within this
WAIT_DEADLINE_MARGIN = 5.0    # WaitComplete's client deadline = its timeout + this
DEFAULT_MDI_TIMEOUT = 60.0    # How long to wait for an MDI command to finish
MAX_MDI_TIMEOUT = 86400.0     # Longest --wait-timeout accepted (one day)
POLL_INTERVAL = 0.05


class ExampleError(Exception):
    """The example cannot continue; the message says why."""


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
        return self.stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=RPC_TIMEOUT)

    def send_command(self, command: linuxcnc_pb2.LinuxCNCCommand) -> linuxcnc_pb2.CommandResponse:
        """Send a command. RCS_DONE in the response means dispatched, not finished."""
        command.serial = self._next_serial()
        command.timestamp = int(time.time() * 1e9)
        return self.stub.SendCommand(command, timeout=RPC_TIMEOUT)

    def wait_complete(
        self, sent: linuxcnc_pb2.CommandResponse, timeout: float
    ) -> linuxcnc_pb2.CommandResponse:
        """Wait until the command that `sent` answers has finished.

        Returns RCS_DONE (finished), RCS_ERROR (failed) or RCS_EXEC (the
        timeout expired and the command is still running).
        """
        request = linuxcnc_pb2.WaitCompleteRequest(timeout=timeout)
        if sent.HasField("linuxcnc_serial"):
            # Wait on this command's serial. WaitCompleteRequest.serial would
            # not do that: the server ignores it.
            request.linuxcnc_serial = sent.linuxcnc_serial
        else:
            print("  Note: the server returned no linuxcnc_serial; waiting on the "
                  "latest command sent by any client instead.")
        # The client deadline must be longer than the wait asked of the server.
        return self.stub.WaitComplete(request, timeout=timeout + WAIT_DEADLINE_MARGIN)

    def set_mode(self, mode: linuxcnc_pb2.TaskMode) -> linuxcnc_pb2.CommandResponse:
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.mode.mode = mode
        return self.send_command(command)

    def mdi(self, gcode: str) -> linuxcnc_pb2.CommandResponse:
        """Send an MDI command."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.mdi.command = gcode
        return self.send_command(command)


def ensure_mdi_ready(client: LinuxCNCClient) -> None:
    """Check the machine is on, homed and idle, then make sure it is in MDI mode."""
    status = client.get_status()

    # Never take the machine out of E-stop or power it on from here.
    if status.task.task_state != linuxcnc_pb2.STATE_ON:
        state_name = linuxcnc_pb2.TaskState.Name(status.task.task_state)
        raise ExampleError(
            f"The machine is not on (state {state_name}). Take it out of E-stop "
            "and turn it on at the machine, then run this again.")

    # A 1.0 server lists 16 joint entries, of which only the first
    # trajectory.joints are real. Newer servers list only those.
    unhomed_joints = [joint.joint_number
                      for joint in status.joints[:status.trajectory.joints]
                      if not joint.homed]
    if unhomed_joints:
        raise ExampleError(
            f"Joints {unhomed_joints} are not homed. Home the machine first; "
            "this example does not home it.")

    # Checked every time, also when the machine is already in MDI mode: a
    # command whose wait timed out is still running, and so may a program be.
    if status.task.interp_state != linuxcnc_pb2.INTERP_IDLE:
        interp_state_name = linuxcnc_pb2.InterpState.Name(status.task.interp_state)
        raise ExampleError(
            "The interpreter is still busy with a previous command or a running "
            f"program (state {interp_state_name}). Nothing was sent; wait for it "
            "to finish and try again.")

    if status.task.task_mode == linuxcnc_pb2.MODE_MDI:
        return

    print("Setting MDI mode...")
    sent = client.set_mode(linuxcnc_pb2.MODE_MDI)
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"MDI mode was refused: {sent.error_message}")

    finished = client.wait_complete(sent, STATE_CHANGE_TIMEOUT)
    if finished.status == linuxcnc_pb2.RCS_EXEC:
        raise ExampleError(f"MDI mode had not finished after {STATE_CHANGE_TIMEOUT:g} s")
    if finished.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"MDI mode failed: {finished.error_message}")

    # Finished is not the same as in effect: check the status, with a deadline.
    deadline = time.monotonic() + STATE_CHANGE_TIMEOUT
    while client.get_status().task.task_mode != linuxcnc_pb2.MODE_MDI:
        if time.monotonic() >= deadline:
            raise ExampleError(
                f"MDI mode did not show in the status within {STATE_CHANGE_TIMEOUT:g} s")
        time.sleep(POLL_INTERVAL)


def execute_mdi(client: LinuxCNCClient, gcode: str, wait: bool, wait_timeout: float) -> None:
    """Send an MDI command and, if asked, wait until it has finished."""
    print(f"Executing: {gcode}")

    sent = client.mdi(gcode)
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"The command was refused: {sent.error_message}")

    if not wait:
        print("  Dispatched (not waiting for it to finish).")
        return

    print("  Waiting for completion...")
    finished = client.wait_complete(sent, wait_timeout)
    if finished.status == linuxcnc_pb2.RCS_DONE:
        print("  Done.")
    elif finished.status == linuxcnc_pb2.RCS_EXEC:
        raise ExampleError(
            f"Timed out: the command was still running after {wait_timeout:g} s. "
            "It has not been cancelled.")
    else:
        raise ExampleError(f"The command failed: {finished.error_message}")


def print_position(client: LinuxCNCClient) -> None:
    pos = client.get_status().position.actual_position
    print(f"Position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")


def interactive_mode(client: LinuxCNCClient, wait_timeout: float) -> int:
    """Interactive MDI shell. Returns how many commands failed.

    A command that fails does not end the session, but it is counted, so the
    caller can exit non-zero.
    """
    failed_command_count = 0
    print("\nInteractive MDI Mode")
    print("Type G-code commands to execute. Type 'quit' or 'exit' to quit.")
    print("Type 'status' to show current position.\n")

    while True:
        try:
            line = input("MDI> ").strip()
        except EOFError:
            print("\nExiting...")
            break
        except (OSError, ValueError) as read_error:
            # A read error is not the end of the input: say so and fail.
            raise ExampleError(f"Reading input failed: {read_error}") from read_error

        if not line:
            continue

        if line.lower() in ("quit", "exit", "q"):
            break

        if line.lower() == "help":
            print("Commands:")
            print("  <G-code>  - Execute G-code command")
            print("  status    - Show current position")
            print("  quit      - Exit interactive mode")
            continue

        try:
            if line.lower() == "status":
                print_position(client)
                continue

            # The machine may have been turned off or switched to another
            # mode since the last command, or the last command may still be
            # running.
            ensure_mdi_ready(client)
            execute_mdi(client, line, wait=True, wait_timeout=wait_timeout)
        except ExampleError as example_error:
            failed_command_count += 1
            print(f"  Error: {example_error}")
        except grpc.RpcError as rpc_error:
            failed_command_count += 1
            print(f"  gRPC error: {rpc_error.code()}: {rpc_error.details()}")

    return failed_command_count


def main():
    parser = argparse.ArgumentParser(description="Execute MDI commands via gRPC")
    parser.add_argument("command", nargs="?", help="G-code command to execute")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Enter interactive MDI mode")
    parser.add_argument("--no-wait", action="store_true",
                        help="Don't wait for command completion")
    parser.add_argument("--wait-timeout", type=float, default=DEFAULT_MDI_TIMEOUT,
                        help="Seconds to wait for a command to finish; more than 0 "
                             "and at most 86400 (default: 60)")
    args = parser.parse_args()

    if not args.command and not args.interactive:
        parser.print_help()
        print("\nExamples:")
        print('  python mdi_command.py "G0 X10 Y10"')
        print('  python mdi_command.py "G1 X20 F100"')
        print('  python mdi_command.py --interactive')
        sys.exit(1)

    # Checked before any RPC: 0 would make the server use its own default, and
    # a negative or huge value would give a client deadline that has already
    # expired or cannot be represented, after the command was dispatched.
    if not (math.isfinite(args.wait_timeout) and 0 < args.wait_timeout <= MAX_MDI_TIMEOUT):
        print("Error: --wait-timeout must be a number of seconds greater than 0 "
              "and at most 86400", file=sys.stderr)
        sys.exit(1)

    client = LinuxCNCClient(args.host, args.port)

    try:
        ensure_mdi_ready(client)

        if args.interactive:
            failed_command_count = interactive_mode(client, args.wait_timeout)
            if failed_command_count:
                raise ExampleError(
                    f"{failed_command_count} command(s) failed in this session.")
        else:
            execute_mdi(client, args.command, wait=not args.no_wait,
                        wait_timeout=args.wait_timeout)
            print_position(client)

    except KeyboardInterrupt:
        print("\nInterrupted. A command that was already sent keeps running "
              "on the machine.", file=sys.stderr)
        sys.exit(130)
    except ExampleError as example_error:
        print(f"Error: {example_error}", file=sys.stderr)
        sys.exit(1)
    except grpc.RpcError as rpc_error:
        print(f"gRPC error: {rpc_error.code()}: {rpc_error.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
