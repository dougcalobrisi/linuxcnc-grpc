#!/usr/bin/env python3
"""
Jog Axis Example

Jogs the X axis by a fixed increment, then jogs the Y axis continuously for
half a second and stops it.

Usage:
    python jog_axis.py [--host HOST] [--port PORT] [--skip-demo]

Before it moves anything the example checks that the machine is on, that
every joint is homed and that the interpreter is idle. It does not take the
machine out of E-stop, power it on or home it: do that at the machine first.
It then switches to manual mode and turns teleop on, which is what an axis
jog needs. The checks are repeated on a fresh status right before each jog
is started.

Safety Warning:
    This script moves the machine! Ensure you have clear access to E-stop
    and understand the jog parameters before running.
"""

import argparse
import os
import signal
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
JOG_STOP_TIMEOUT = 2.0        # The jog stop has its own short deadline
STATE_CHANGE_TIMEOUT = 5.0    # A mode or teleop change must finish within this
WAIT_DEADLINE_MARGIN = 5.0    # WaitComplete's client deadline = its timeout + this
MOTION_START_TIMEOUT = 2.0    # A jog that has not moved by now was refused
MOTION_TIMEOUT = 10.0         # A jog must be finished and stopped within this
POLL_INTERVAL = 0.05
INTERRUPT_CHECK_INTERVAL = 0.02  # How often a sleep looks at the interrupt flag

# The demo moves. Jog velocity is in machine units per second.
AXIS_LETTERS = "XYZABCUVW"
INCREMENT_AXIS = 0            # X
CONTINUOUS_AXIS = 1           # Y
JOG_VELOCITY = 1.0            # Machine units per second
INCREMENT_DISTANCE = 1.0      # Machine units
CONTINUOUS_JOG_SECONDS = 0.5
MOVED_THRESHOLD = 0.01        # Machine units; more than feedback jitter, far less than either jog
STOPPED_VELOCITY = 1e-6

# Set by Ctrl+C or SIGTERM. The handler only sets this flag; the waits below
# check it, so the program always leaves through the code that stops the jog.
# It is a plain boolean on purpose. A signal handler runs in the main thread
# between two instructions of whatever was running, so it must not take a
# lock: threading.Event.set() does, and would block forever if the signal
# arrived while Event.wait() held that same lock.
interrupt_was_requested = False


class ExampleError(Exception):
    """The example cannot continue; the message says why."""


class Interrupted(Exception):
    """Ctrl+C or SIGTERM arrived."""


def install_interrupt_handlers() -> None:
    def remember_interrupt(signal_number, frame):
        # No lock, no output, no exception: only the flag.
        global interrupt_was_requested
        interrupt_was_requested = True

    signal.signal(signal.SIGINT, remember_interrupt)
    signal.signal(signal.SIGTERM, remember_interrupt)


def pause(seconds: float) -> None:
    """Sleep in short slices; raise Interrupted soon after an interrupt arrives."""
    wake_time = time.monotonic() + seconds
    while True:
        if interrupt_was_requested:
            raise Interrupted()
        remaining = wake_time - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, INTERRUPT_CHECK_INTERVAL))


def report(text: str, stream=None) -> None:
    """Print, but never raise because the output has gone away.

    If the reader of a pipe has closed it, print() raises BrokenPipeError.
    Everything printed after the first jog has started goes through here, so
    a lost terminal or pipe cannot get in the way of stopping the axis.
    """
    stream = stream or sys.stdout
    try:
        print(text, file=stream, flush=True)
    except (OSError, ValueError):
        # Point the stream at nothing, so the interpreter's flush at exit
        # does not fail on it again.
        try:
            null_device = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null_device, stream.fileno())
            os.close(null_device)
        except (OSError, ValueError):
            pass


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
        return self.stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=RPC_TIMEOUT)

    def send_command(
        self, command: linuxcnc_pb2.LinuxCNCCommand, timeout: float = RPC_TIMEOUT
    ) -> linuxcnc_pb2.CommandResponse:
        """Send a command. RCS_DONE in the response means dispatched, not finished."""
        command.serial = self._next_serial()
        command.timestamp = int(time.time() * 1e9)
        return self.stub.SendCommand(command, timeout=timeout)

    def wait_complete(
        self, sent: linuxcnc_pb2.CommandResponse, timeout: float
    ) -> linuxcnc_pb2.CommandResponse:
        """Wait until the command that `sent` answers has finished."""
        request = linuxcnc_pb2.WaitCompleteRequest(timeout=timeout)
        if sent.HasField("linuxcnc_serial"):
            # Wait on this command's serial.
            request.linuxcnc_serial = sent.linuxcnc_serial
        else:
            print("  Note: the server returned no linuxcnc_serial; waiting on the "
                  "latest command sent by any client instead.")
        # The client deadline must be longer than the wait asked of the server.
        return self.stub.WaitComplete(request, timeout=timeout + WAIT_DEADLINE_MARGIN)

    def set_mode(self, mode: linuxcnc_pb2.TaskMode) -> linuxcnc_pb2.CommandResponse:
        """Set task mode (MANUAL, AUTO, MDI)."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.mode.mode = mode
        return self.send_command(command)

    def set_teleop(self, enable: bool) -> linuxcnc_pb2.CommandResponse:
        """Turn teleop on (axis jogs) or off (joint jogs, homing)."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.teleop.enable = enable
        return self.send_command(command)

    def jog_continuous(self, axis: int, velocity: float) -> linuxcnc_pb2.CommandResponse:
        """Start a continuous axis jog. velocity > 0 for positive, < 0 for negative."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.jog.type = linuxcnc_pb2.JOG_CONTINUOUS
        command.jog.is_joint = False  # Axis jog: needs teleop on
        command.jog.index = axis      # 0=X, 1=Y, 2=Z, etc.
        command.jog.velocity = velocity
        return self.send_command(command)

    def jog_increment(self, axis: int, velocity: float, distance: float) -> linuxcnc_pb2.CommandResponse:
        """Jog an axis by a fixed distance."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.jog.type = linuxcnc_pb2.JOG_INCREMENT
        command.jog.is_joint = False
        command.jog.index = axis
        command.jog.velocity = velocity
        command.jog.increment = distance
        return self.send_command(command)

    def jog_stop(self, axis: int) -> linuxcnc_pb2.CommandResponse:
        """Stop jogging an axis. Uses its own short deadline."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.jog.type = linuxcnc_pb2.JOG_STOP
        command.jog.is_joint = False
        command.jog.index = axis
        return self.send_command(command, timeout=JOG_STOP_TIMEOUT)


def axis_position(status: linuxcnc_pb2.LinuxCNCStatus, axis: int) -> float:
    return getattr(status.position.actual_position, AXIS_LETTERS[axis].lower())


def recent_operator_messages(status: linuxcnc_pb2.LinuxCNCStatus) -> str:
    """The newest messages the server has read from LinuxCNC, if it reads them."""
    if not status.errors:
        return "Look at the operator messages in the LinuxCNC GUI for the reason."
    return "Recent LinuxCNC messages: " + "; ".join(
        error.message for error in list(status.errors)[-3:])


def change_and_confirm(client: LinuxCNCClient, sent: linuxcnc_pb2.CommandResponse,
                       description: str, is_confirmed) -> None:
    """Wait for a mode or teleop command to finish, then check the status shows it."""
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"{description} was refused: {sent.error_message}")

    finished = client.wait_complete(sent, STATE_CHANGE_TIMEOUT)
    if finished.status == linuxcnc_pb2.RCS_EXEC:
        raise ExampleError(f"{description} had not finished after {STATE_CHANGE_TIMEOUT:g} s")
    if finished.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"{description} failed: {finished.error_message}")

    # Finished is not the same as in effect: check the status, with a deadline.
    deadline = time.monotonic() + STATE_CHANGE_TIMEOUT
    while not is_confirmed(client.get_status()):
        if time.monotonic() >= deadline:
            raise ExampleError(
                f"{description} did not show in the status within {STATE_CHANGE_TIMEOUT:g} s")
        pause(POLL_INTERVAL)


def require_machine_on(status: linuxcnc_pb2.LinuxCNCStatus) -> None:
    """Raise unless the machine is on. It is never turned on from here."""
    if status.task.task_state != linuxcnc_pb2.STATE_ON:
        state_name = linuxcnc_pb2.TaskState.Name(status.task.task_state)
        raise ExampleError(
            f"The machine is not on (state {state_name}). Take it out of E-stop "
            "and turn it on at the machine, then run this again.")


def machine_is_busy(status: linuxcnc_pb2.LinuxCNCStatus) -> bool:
    """True while a program, an MDI command or a jog is under way.

    The interpreter state alone does not tell. On the LinuxCNC 2.9 simulator
    it read INTERP_IDLE for the whole of some MDI moves, while
    trajectory.inpos was false in every reading taken during a move. A jog
    leaves inpos true, so velocity is looked at as well.
    """
    return (status.task.interp_state != linuxcnc_pb2.INTERP_IDLE
            or not status.trajectory.inpos
            or abs(status.trajectory.current_vel) >= STOPPED_VELOCITY)


def ready_status_for_jog(client: LinuxCNCClient) -> linuxcnc_pb2.LinuxCNCStatus:
    """Fetch a fresh status and check that the machine is on, homed and idle.

    Called before the mode is changed and again right before every jog is
    started: the machine can be put in E-stop, turned off or given a program
    at any moment, so an earlier answer is not good enough. Only the start of
    a jog depends on this check; a jog stop is always sent.
    """
    status = client.get_status()

    # Never take the machine out of E-stop or power it on from here.
    require_machine_on(status)

    # A 1.0 server lists 16 joint entries, of which only the first
    # trajectory.joints are real. Newer servers list only those.
    # An axis jog needs teleop, and teleop needs every joint homed.
    unhomed_joints = [joint.joint_number
                      for joint in status.joints[:status.trajectory.joints]
                      if not joint.homed]
    if unhomed_joints:
        raise ExampleError(
            f"Joints {unhomed_joints} are not homed. Home the machine first; "
            "this example does not home it.")

    for axis in (INCREMENT_AXIS, CONTINUOUS_AXIS):
        if not status.trajectory.axis_mask & (1 << axis):
            raise ExampleError(f"This machine has no {AXIS_LETTERS[axis]} axis.")

    # Checked whatever the mode is: never jog, or change mode, under a
    # running program, MDI command or jog.
    if machine_is_busy(status):
        interp_state_name = linuxcnc_pb2.InterpState.Name(status.task.interp_state)
        raise ExampleError(
            "The machine is busy with a program, an MDI command or a jog "
            f"(interpreter {interp_state_name}, in position: {status.trajectory.inpos}, "
            f"velocity {status.trajectory.current_vel:g}). The jog was not started; "
            "wait for it to finish and run this again.")

    return status


def prepare_for_axis_jog(client: LinuxCNCClient) -> None:
    """Check the machine is on, homed and idle, then set manual mode and teleop."""
    status = ready_status_for_jog(client)

    if status.task.task_mode != linuxcnc_pb2.MODE_MANUAL:
        print("Setting manual mode...")
        change_and_confirm(
            client, client.set_mode(linuxcnc_pb2.MODE_MANUAL), "Manual mode",
            lambda new_status: new_status.task.task_mode == linuxcnc_pb2.MODE_MANUAL)
        status = client.get_status()

    if status.trajectory.motion_mode != linuxcnc_pb2.TRAJ_MODE_TELEOP:
        print("Turning teleop on...")
        change_and_confirm(
            client, client.set_teleop(True), "Teleop",
            lambda new_status: new_status.trajectory.motion_mode == linuxcnc_pb2.TRAJ_MODE_TELEOP)


def stop_jog(client: LinuxCNCClient, axis: int) -> None:
    """Send the jog stop and insist that it was dispatched."""
    axis_letter = AXIS_LETTERS[axis]
    try:
        sent = client.jog_stop(axis)
    except grpc.RpcError as rpc_error:
        raise ExampleError(
            f"The jog stop for {axis_letter} did not get through ({rpc_error.code()}). "
            "The axis may still be moving: use the E-stop.") from rpc_error
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(
            f"The jog stop for {axis_letter} was refused: {sent.error_message}. "
            "The axis may still be moving: use the E-stop.")


def wait_for_motion_to_end(client: LinuxCNCClient, axis: int, start_position: float) -> float:
    """Wait until the axis has moved and stopped again; return its position.

    A jog that LinuxCNC refuses still answers RCS_DONE, so the only proof of a
    jog is the status. trajectory.inpos stays true during a jog; velocity is
    what shows motion.

    The machine state is checked on every poll. After an E-stop the axis
    stops at once, but the velocity in the status keeps its last value until
    the machine is turned on again, so velocity alone would never show the
    stop.
    """
    axis_letter = AXIS_LETTERS[axis]
    started_waiting = time.monotonic()
    while True:
        status = client.get_status()
        require_machine_on(status)
        position = axis_position(status, axis)
        has_moved = abs(position - start_position) > MOVED_THRESHOLD
        is_stopped = abs(status.trajectory.current_vel) < STOPPED_VELOCITY
        if has_moved and is_stopped:
            return position

        waited = time.monotonic() - started_waiting
        if not has_moved and waited >= MOTION_START_TIMEOUT:
            raise ExampleError(
                f"{axis_letter} did not move, so LinuxCNC refused the jog. "
                + recent_operator_messages(status))
        if waited >= MOTION_TIMEOUT:
            raise ExampleError(f"{axis_letter} was still moving after {MOTION_TIMEOUT:g} s")
        pause(POLL_INTERVAL)


def demo_incremental_jog(client: LinuxCNCClient) -> None:
    """Jog one axis by a fixed distance and wait until it has arrived."""
    axis_letter = AXIS_LETTERS[INCREMENT_AXIS]
    print("\n--- Incremental Jog Demo ---")
    print(f"Jogging {axis_letter} by +{INCREMENT_DISTANCE:g} at {JOG_VELOCITY:g} units/s...")

    # The check is repeated here, on a fresh status, right before the start.
    start_position = axis_position(ready_status_for_jog(client), INCREMENT_AXIS)
    pause(0)  # Do not start a jog after an interrupt
    try:
        # Nothing is printed between here and the stop: a failed print must
        # not come before it.
        sent = client.jog_increment(INCREMENT_AXIS, JOG_VELOCITY, INCREMENT_DISTANCE)
        if sent.status != linuxcnc_pb2.RCS_DONE:
            raise ExampleError(f"Incremental jog was refused: {sent.error_message}")
        end_position = wait_for_motion_to_end(client, INCREMENT_AXIS, start_position)
    except BaseException:
        # Interrupted or failed part way (including a start whose answer never
        # came): do not leave the axis moving. When the wait succeeds no stop
        # is needed: the status showed the axis moved and is standing still.
        stop_jog(client, INCREMENT_AXIS)
        raise

    report(f"{axis_letter} moved from {start_position:.4f} to {end_position:.4f}")


def demo_continuous_jog(client: LinuxCNCClient) -> None:
    """Jog one axis continuously for a moment, then stop it."""
    axis_letter = AXIS_LETTERS[CONTINUOUS_AXIS]
    report("\n--- Continuous Jog Demo ---")
    report(f"Jogging {axis_letter} at +{JOG_VELOCITY:g} units/s "
           f"for {CONTINUOUS_JOG_SECONDS:g} seconds...")

    # The check is repeated here, on a fresh status, right before the start.
    start_position = axis_position(ready_status_for_jog(client), CONTINUOUS_AXIS)
    pause(0)  # Do not start a jog after an interrupt
    try:
        # From here on the stop below is always sent: after the sleep, on
        # Ctrl+C and on any error, including a start whose answer never came.
        # Nothing is printed until the stop has been sent: a failed print
        # must not come before it.
        sent = client.jog_continuous(CONTINUOUS_AXIS, JOG_VELOCITY)
        if sent.status != linuxcnc_pb2.RCS_DONE:
            raise ExampleError(f"Continuous jog was refused: {sent.error_message}")
        pause(CONTINUOUS_JOG_SECONDS)
    finally:
        stop_jog(client, CONTINUOUS_AXIS)
    report("Jog stop sent.")

    end_position = wait_for_motion_to_end(client, CONTINUOUS_AXIS, start_position)
    report(f"{axis_letter} moved from {start_position:.4f} to {end_position:.4f}")


def main():
    parser = argparse.ArgumentParser(description="Jog axis demo via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("--skip-demo", action="store_true",
                        help="Skip the demo movements (just show status)")
    args = parser.parse_args()

    # Installed before anything moves.
    install_interrupt_handlers()
    client = LinuxCNCClient(args.host, args.port)

    try:
        # Show initial status
        status = client.get_status()
        pos = status.position.actual_position
        print(f"Current position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")

        if args.skip_demo:
            print("Skipping demo movements (--skip-demo)")
            return

        prepare_for_axis_jog(client)
        demo_incremental_jog(client)
        demo_continuous_jog(client)

        report("\nJog demo complete!")

    except Interrupted:
        report("\nInterrupted.", sys.stderr)
        sys.exit(130)
    except ExampleError as example_error:
        report(f"Error: {example_error}", sys.stderr)
        sys.exit(1)
    except grpc.RpcError as rpc_error:
        report(f"gRPC error: {rpc_error.code()}: {rpc_error.details()}", sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
