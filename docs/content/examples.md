---
title: "Examples Guide"
weight: 50
---

Walkthrough of the example code provided with linuxcnc-grpc.

## Overview

Examples are provided in four languages, each implementing the same functionality:

| Example | Description |
|---------|-------------|
| `get_status` | Poll and display machine status |
| `stream_status` | Real-time status streaming |
| `jog_axis` | Jog an axis (incremental and continuous) |
| `mdi_command` | Execute G-code via MDI |
| `hal_query` | Query HAL pins, signals, parameters |
| `upload_file` | Upload, list, and delete G-code files |

## Directory Structure

```
examples/
├── python/
│   ├── get_status.py
│   ├── stream_status.py
│   ├── jog_axis.py
│   ├── mdi_command.py
│   ├── hal_query.py
│   └── upload_file.py
├── go/
│   └── cmd/
│       ├── get_status/
│       ├── stream_status/
│       ├── jog_axis/
│       ├── mdi_command/
│       ├── hal_query/
│       └── upload_file/
├── node/
│   ├── get_status.ts
│   ├── stream_status.ts
│   ├── jog_axis.ts
│   ├── mdi_command.ts
│   ├── hal_query.ts
│   └── upload_file.ts
├── rust/
│   ├── Cargo.toml
│   └── src/bin/
│       ├── get_status.rs
│       ├── stream_status.rs
│       ├── jog_axis.rs
│       ├── mdi_command.rs
│       ├── hal_query.rs
│       └── upload_file.rs
└── README.md
```

## Running Examples

### Prerequisites

1. LinuxCNC running (or use a mock server for testing)
2. gRPC server running on the LinuxCNC machine
3. Language-specific dependencies installed

{{%  tabs "run-examples"  %}}
{{%  tab "Python"  %}}
```bash
cd examples/python

# Install dependencies (if using virtualenv)
pip install grpcio linuxcnc-grpc

# Run an example
python get_status.py --host localhost --port 50051
```
{{%  /tab  %}}
{{%  tab "Go"  %}}
```bash
cd examples/go

# Download dependencies
go mod download

# Run an example
go run ./cmd/get_status --host localhost --port 50051
```
{{%  /tab  %}}
{{%  tab "Node.js / TypeScript"  %}}
```bash
cd examples/node

# Install dependencies
npm install

# Run an example
npx tsx get_status.ts --host localhost --port 50051
```
{{%  /tab  %}}
{{%  tab "Rust"  %}}
```bash
cd examples/rust

# Build all examples
cargo build --release

# Run an example
cargo run --bin get_status -- --host localhost --port 50051

# Or run the built binary directly
./target/release/get_status --host localhost --port 50051
```
{{%  /tab  %}}
{{%  /tabs  %}}

---

## get_status

The simplest example - polls the machine once and displays status.

### What it demonstrates

- Connecting to the gRPC server
- Calling `GetStatus` RPC
- Parsing the `LinuxCNCStatus` response
- Displaying task state, position, joints, spindles, and I/O

### Key code (Python)

```python
# Connect
channel = grpc.insecure_channel(f"{host}:{port}")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

# Request status, with a deadline
status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=5.0)

# Access nested fields
print(f"Mode: {linuxcnc_pb2.TaskMode.Name(status.task.task_mode)}")
print(f"X: {status.position.actual_position.x:.4f}")
```

### Output

```
============================================================
LinuxCNC Status
============================================================

[Task]
  Mode:       MODE_MANUAL
  State:      STATE_ON
  Exec State: EXEC_DONE
  Interp:     INTERP_IDLE

[Position]
  X:     0.0000  Y:     0.0000  Z:     0.0000

[Trajectory]
  Enabled:    True
  Feed Rate:  100.0%
  Rapid Rate: 100.0%
  Velocity:   0.00

[Joints]
  Joint 0: [HE-] pos=    0.0000
  Joint 1: [HE-] pos=    0.0000
  Joint 2: [HE-] pos=    0.0000
```

---

## stream_status

Real-time status streaming instead of polling.

### What it demonstrates

- Using `StreamStatus` server-streaming RPC
- Setting update interval
- Processing a continuous stream of updates
- Calculating update rate
- Giving up when the stream goes silent, and telling a stream the server
  cancelled from one the example cancelled itself (see
  [Streaming Timeouts](#streaming-timeouts))

### Key code

{{%  tabs "stream-key-code"  %}}
{{%  tab "Python"  %}}
```python
# Start streaming with 100ms interval
request = linuxcnc_pb2.StreamStatusRequest(interval_ms=100)

for status in stub.StreamStatus(request):
    pos = status.position.actual_position
    print(f"\rX={pos.x:8.3f} Y={pos.y:8.3f} Z={pos.z:8.3f}", end="")
```
{{%  /tab  %}}
{{%  tab "Go"  %}}
```go
// Cancelling ctx (the example does it on Ctrl+C) ends the stream.
ctx, cancel := context.WithCancel(context.Background())
defer cancel()

stream, err := client.StreamStatus(ctx, &pb.StreamStatusRequest{
    IntervalMs: 100,
})
if err != nil {
    log.Fatalf("StreamStatus failed: %v", err)
}

for {
    status, err := stream.Recv()
    if err == io.EOF {
        break
    }
    if err != nil {
        if ctx.Err() != nil {
            break // cancelled by this program
        }
        log.Fatalf("Stream error: %v", err)
    }
    pos := status.Position.ActualPosition
    fmt.Printf("X=%.3f Y=%.3f Z=%.3f\n", pos.X, pos.Y, pos.Z)
}
```
{{%  /tab  %}}
{{%  /tabs  %}}

### Output

```
Streaming status from localhost:50051 (interval: 100ms)
Press Ctrl+C to stop

--------------------------------------------------------------------------------
[    42] X:   0.500 Y:   0.000 Z:   0.000 | MANUAL/ON/IDLE       | V:  10.00 F:100.0%
```

The line is rewritten in place for every update. After Ctrl+C the example
prints how many updates it received and the rate, and exits 0.

### Usage

```bash
# One update every 100 ms (the default)
python stream_status.py

# One update every 2 seconds
python stream_status.py --interval 2000
```

`--interval` is in milliseconds and must be a whole number from 1 to 60000
(the server's maximum); anything else is refused before the stream is opened.
The exit code is non-zero when the stream fails, when the server cancels it,
and when no update arrives in time (see
[Streaming Timeouts](#streaming-timeouts)).

---

## jog_axis

Jogs the X axis by a fixed increment, then jogs the Y axis continuously for
half a second and stops it. It runs the two moves and exits; it is not a
keyboard jog pendant.

### What it demonstrates

- Checking that the machine is on, that every joint is homed and that the
  interpreter is idle, and stopping with a clear message if not. The example
  never resets E-stop, powers the machine on or homes it, and never jogs or
  changes mode under a running program or MDI command.
- Telling "idle" from the status: the interpreter is idle, `trajectory.inpos`
  is true and `trajectory.current_vel` is zero. The interpreter state alone
  is not enough: on the 2.9 simulator it read `INTERP_IDLE` for the whole of
  some MDI moves (see
  [Modes and teleop](api-reference.md#modes-and-teleop-what-to-send-first)).
- Repeating that check on a fresh status right before each jog is started.
  The machine can be put in E-stop, turned off or given a program between
  the first check and a later jog, so the answer from startup is not reused.
  Only the start of a jog depends on the check; a jog stop is always sent.
- Setting manual mode and turning teleop on, which an axis jog needs
  (see [Modes and teleop](api-reference.md#modes-and-teleop-what-to-send-first))
- Waiting for a mode change with `WaitComplete` and the command's
  `linuxcnc_serial`, then confirming it in the status
- Incremental jogging with `JOG_INCREMENT`. A `JOG_STOP` follows if the jog
  is interrupted or fails part way. An incremental jog that completed
  normally, with the status showing that the axis moved and is standing
  still, gets no stop: it has none to wait for.
- Continuous jogging with `JOG_CONTINUOUS`, and a `JOG_STOP` that is sent on
  every way out of it: after the jog time, on Ctrl+C or SIGTERM, after an
  error, and when the terminal or pipe the example prints to has gone away
- Confirming motion from the status, because a jog that LinuxCNC refuses still
  answers `RCS_DONE`
- Checking the machine state on every poll while waiting for an axis to stop.
  After an E-stop the axis stops at once, but `trajectory.current_vel` keeps
  its last value until the machine is turned on again, so a wait on velocity
  alone would run into its timeout. The example stops waiting at once and
  exits with the "machine is not on" message.

Jog velocity is in machine units per second. The example uses 1 unit per
second and a 1 unit increment; change the constants at the top of the file to
suit the machine.

### Key code (Python)

The snippets below are taken from `examples/python/jog_axis.py` and use these
constants from the top of that file:

```python
# Deadlines, in seconds. Every RPC gets one.
RPC_TIMEOUT = 5.0             # GetStatus and SendCommand
JOG_STOP_TIMEOUT = 2.0        # The jog stop has its own short deadline
STATE_CHANGE_TIMEOUT = 5.0    # A mode or teleop change must finish within this
WAIT_DEADLINE_MARGIN = 5.0    # WaitComplete's client deadline = its timeout + this
POLL_INTERVAL = 0.05

CONTINUOUS_AXIS = 1           # Y
JOG_VELOCITY = 1.0            # Machine units per second
CONTINUOUS_JOG_SECONDS = 0.5
```

`wait_complete`, `set_teleop`, `jog_continuous` and `jog_stop` are methods of
the `LinuxCNCClient` wrapper shown under
[Client Wrapper Class](#client-wrapper-class). `ExampleError` is the example's
own exception (`class ExampleError(Exception)`). `pause` and `Interrupted`
are shown under [Stopping Motion on Ctrl+C](#stopping-motion-on-ctrlc). Three
more helpers are used but not repeated here; they are in the file:
`report(text)` is a `print` that ignores a failed write,
`axis_position(status, axis)` returns one axis of
`status.position.actual_position`, and
`wait_for_motion_to_end(client, axis, start_position)` polls `GetStatus`
until the axis has moved and `trajectory.current_vel` is back to zero; on
every poll it also calls `require_machine_on(status)`, which raises the
"machine is not on" error.

`ready_status_for_jog(client)` fetches a fresh status, raises `ExampleError`
unless the machine is on, homed and idle, and returns the status. It runs
before the mode is changed and again right before each jog is started:

```python
def require_machine_on(status):
    """Raise unless the machine is on. It is never turned on from here."""
    if status.task.task_state != linuxcnc_pb2.STATE_ON:
        state_name = linuxcnc_pb2.TaskState.Name(status.task.task_state)
        raise ExampleError(
            f"The machine is not on (state {state_name}). Take it out of E-stop "
            "and turn it on at the machine, then run this again.")


def machine_is_busy(status):
    """True while a program, an MDI command or a jog is under way."""
    return (status.task.interp_state != linuxcnc_pb2.INTERP_IDLE
            or not status.trajectory.inpos
            or abs(status.trajectory.current_vel) >= STOPPED_VELOCITY)


def ready_status_for_jog(client):
    status = client.get_status()
    require_machine_on(status)

    # ... every joint is homed, the machine has the two axes ...

    if machine_is_busy(status):
        interp_state_name = linuxcnc_pb2.InterpState.Name(status.task.interp_state)
        raise ExampleError(
            "The machine is busy with a program, an MDI command or a jog "
            f"(interpreter {interp_state_name}, in position: {status.trajectory.inpos}, "
            f"velocity {status.trajectory.current_vel:g}). The jog was not started; "
            "wait for it to finish and run this again.")

    return status
```

`STOPPED_VELOCITY` is `1e-6`. `mdi_command` has the same `machine_is_busy`
and uses it before every command and to confirm that a command has finished.

Switching mode: send, wait on that command's serial, then check the status.

```python
def wait_complete(self, sent, timeout):
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


def change_and_confirm(client, sent, description, is_confirmed):
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


change_and_confirm(
    client, client.set_teleop(True), "Teleop",
    lambda new_status: new_status.trajectory.motion_mode == linuxcnc_pb2.TRAJ_MODE_TELEOP)
```

The continuous jog. The stop is in a `finally`, and the interrupt handlers are
installed before anything moves, so Ctrl+C leaves through the same code:

```python
def jog_continuous(self, axis, velocity):
    command = linuxcnc_pb2.LinuxCNCCommand()
    command.jog.type = linuxcnc_pb2.JOG_CONTINUOUS
    command.jog.is_joint = False  # Axis jog: needs teleop on
    command.jog.index = axis      # 0=X, 1=Y, 2=Z, etc.
    command.jog.velocity = velocity
    return self.send_command(command)

def jog_stop(self, axis):
    command = linuxcnc_pb2.LinuxCNCCommand()
    command.jog.type = linuxcnc_pb2.JOG_STOP
    command.jog.is_joint = False
    command.jog.index = axis
    return self.send_command(command, timeout=JOG_STOP_TIMEOUT)  # its own short deadline


def stop_jog(client, axis):
    """Send the jog stop and insist that it was dispatched."""
    axis_letter = AXIS_LETTERS[axis]    # AXIS_LETTERS = "XYZABCUVW"
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


# The check is repeated here, on a fresh status, right before the start.
start_position = axis_position(ready_status_for_jog(client), CONTINUOUS_AXIS)
pause(0)  # Do not start a jog after an interrupt
try:
    sent = client.jog_continuous(CONTINUOUS_AXIS, JOG_VELOCITY)
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"Continuous jog was refused: {sent.error_message}")
    pause(CONTINUOUS_JOG_SECONDS)   # raises Interrupted on Ctrl+C
finally:
    stop_jog(client, CONTINUOUS_AXIS)
report("Jog stop sent.")            # a print that cannot raise

# RCS_DONE does not prove the jog happened: confirm it in the status.
end_position = wait_for_motion_to_end(client, CONTINUOUS_AXIS, start_position)
```

The Go, Node.js and Rust versions do the same: the stop is sent with its own
fresh deadline whatever happened before it. Nothing is printed between the
start of a jog and its stop; see
[Stopping Motion on Ctrl+C](#stopping-motion-on-ctrlc) for why.

The exit code is 0 after both jogs, 130 after Ctrl+C or SIGTERM once the stop
went out, and 1 after any error. A jog stop that fails always gives 1, also
when Ctrl+C was what led to it, with a message that says the axis may still
be moving and to use the E-stop.

### Usage

```bash
# Run both jogs (moves the machine!)
python jog_axis.py

# Only show the current position
python jog_axis.py --skip-demo
```

### Output

```
Current position: X=0.0000 Y=0.0000 Z=0.0000
Turning teleop on...

--- Incremental Jog Demo ---
Jogging X by +1 at 1 units/s...
X moved from 0.0000 to 1.0000

--- Continuous Jog Demo ---
Jogging Y at +1 units/s for 0.5 seconds...
Jog stop sent.
Y moved from 0.0000 to 0.4400

Jog demo complete!
```

If the machine is not ready the example stops without starting a jog. At
startup that is before anything is sent; before the second jog it is after
the first one has run:

```
Error: The machine is not on (state STATE_ESTOP). Take it out of E-stop and turn it on at the machine, then run this again.
```

```
Error: The machine is busy with a program, an MDI command or a jog (interpreter INTERP_READING, in position: False, velocity 1). The jog was not started; wait for it to finish and run this again.
```

---

## mdi_command

Execute G-code commands via MDI (Manual Data Input).

### What it demonstrates

- Checking that the machine is on, that every joint is homed and that the
  interpreter is idle, before every command, and stopping with a clear
  message if not. The example never resets E-stop, powers the machine on or
  homes it.
- Setting MDI mode, waiting for the switch and confirming it in the status
- Sending MDI commands
- Using `WaitComplete` with the command's `linuxcnc_serial` to wait on that
  command's serial. The answer is exact as long as nothing else is sent to
  LinuxCNC meanwhile (see
  [Command and Error Semantics](server.md#waitcomplete))
- Confirming completion from the status after the wait answers `RCS_DONE`.
  The server gives that answer as soon as LinuxCNC has taken a later command
  from any client (the GUI changing the feed override, for example), and the
  MDI move may then still be running. So the example polls `GetStatus` until
  the machine is idle (`task.interp_state` is `INTERP_IDLE`,
  `trajectory.inpos` is true and `trajectory.current_vel` is zero), within
  what is left of `--wait-timeout`, and prints `Done.` only then.
- Requiring the machine to be still on when it confirms. An E-stop from
  another client also ends the wait with `RCS_DONE`, and the status then
  reads idle, in position and standing still; the example reports "The
  machine is no longer on ... The command was stopped and did not finish."
  and exits non-zero.
- Telling the five outcomes apart: finished, failed, timed out, stopped by
  an E-stop or power-off, and not confirmed (the wait ended but the status
  did not settle in time)
- A limit: an abort from another client cannot be told from completion (see
  below)
- Interactive command loop

### Key code (Python)

`wait_complete` is a method of the example's `LinuxCNCClient` wrapper, and
`client.mdi` is the wrapper's method that sends an `MdiCommand` (both under
[Client Wrapper Class](#client-wrapper-class)). `WAIT_DEADLINE_MARGIN` is 5
seconds, `POLL_INTERVAL` is 0.05 seconds and `ExampleError` is the example's
own exception.

```python
def wait_complete(self, sent, timeout):
    """Wait until the command that `sent` answers has finished.

    Returns RCS_DONE, RCS_ERROR (failed) or RCS_EXEC (the timeout
    expired and the command is still running). RCS_DONE means finished
    only if LinuxCNC took no later command meanwhile; see
    confirm_mdi_finished.
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


def confirm_mdi_finished(client, deadline):
    """Poll the status until it shows that the MDI command is no longer running.

    Returns False if the status did not show that by `deadline` (a
    time.monotonic() value). The status is read at least once, and each read
    has its own deadline. Raises ExampleError if the machine is not on.
    """
    while True:
        status = client.get_status()
        if not machine_is_on(status):
            state_name = linuxcnc_pb2.TaskState.Name(status.task.task_state)
            raise ExampleError(
                f"The machine is no longer on (state {state_name}). The command "
                "was stopped and did not finish.")
        if not machine_is_busy(status):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def execute_mdi(client, gcode, wait, wait_timeout):
    """Send an MDI command and, if asked, wait until it has finished."""
    print(f"Executing: {gcode}")

    # RCS_DONE here means dispatched, not finished.
    sent = client.mdi(gcode)
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"The command was refused: {sent.error_message}")

    if not wait:
        print("  Dispatched (not waiting for it to finish).")
        return

    print("  Waiting for completion...")
    # One timeout covers both the wait and the check of the status after it.
    wait_deadline = time.monotonic() + wait_timeout
    finished = client.wait_complete(sent, wait_timeout)
    if finished.status == linuxcnc_pb2.RCS_EXEC:
        raise ExampleError(
            f"Timed out: the command was still running after {wait_timeout:g} s. "
            "It has not been cancelled.")
    if finished.status != linuxcnc_pb2.RCS_DONE:
        raise ExampleError(f"The command failed: {finished.error_message}")

    if not confirm_mdi_finished(client, wait_deadline):
        raise ExampleError(
            "Not confirmed: the wait ended, but the status did not show the "
            f"interpreter idle and the motion in position within {wait_timeout:g} s. "
            "The command may still be running (a wait ends early when LinuxCNC "
            "takes a later command from any client). It has not been cancelled.")
    print("  Done.")
```

`RCS_DONE` from the wait is not the last word. If nothing else was sent to
LinuxCNC during the wait, the command has finished and the first status read
confirms it. If LinuxCNC took a later command meanwhile, the wait has ended
early, and `confirm_mdi_finished` keeps reading the status until
`machine_is_busy` (shown under [jog_axis](#jog_axis)) is false: the
interpreter is idle, the motion is in position and the velocity is zero. The wait and the
confirmation share one `--wait-timeout`; the last status read can add its own
5 second deadline at most. The status shows that the command is no longer
running. It does not show whether a command whose wait ended early succeeded
or failed: for that, look at the operator messages
([`StreamErrors`](api-reference.md#streamerrors) or `status.errors`).

`machine_is_on(status)` is `status.task.task_state == linuxcnc_pb2.STATE_ON`.
The confirmation needs it because of what an E-stop looks like. Measured on
the 2.9 simulator: an E-stop sent by another client during an MDI move ended
the wait with `RCS_DONE` at once, and the status read interpreter idle, in
position and velocity zero, with the axis a third of the way to its target.
Without the check the example printed `Done.`.

**What the example cannot detect.** An abort from another client (the GUI's
stop button, for example) also ends the wait with `RCS_DONE` and stops the
move, but it leaves the machine on, idle and in position, which is exactly
what a finished command looks like. In that case the example prints `Done.`
although the move ended short of its target. Only the position shows it. The
example does not parse the G-code for a target, so it does not check; a
client that knows where its move should end can compare
`position.actual_position` with that after the wait.

`ensure_mdi_ready` runs before every command. It refuses to go on unless the
machine is on, homed and idle, and switches to MDI mode the same way
`jog_axis` switches to manual: send, `WaitComplete`, then check
`task.task_mode`. The idle check comes before the mode check, so it also runs
when the machine is already in MDI mode:

```python
status = client.get_status()
# ... the machine is on, every joint is homed ...

# A command whose wait timed out is still running, and so may a program be.
# The interpreter state alone does not show a running MDI move reliably;
# machine_is_busy also looks at trajectory.inpos and the velocity.
if machine_is_busy(status):
    interp_state_name = linuxcnc_pb2.InterpState.Name(status.task.interp_state)
    raise ExampleError(
        "The machine is still busy with a previous command, a running program "
        f"or a jog (interpreter {interp_state_name}, in position: "
        f"{status.trajectory.inpos}, velocity {status.trajectory.current_vel:g}). "
        "Nothing was sent; wait for it to finish and try again.")

if status.task.task_mode == linuxcnc_pb2.MODE_MDI:
    return
# ... otherwise switch to MDI mode and confirm it ...
```

That matters in interactive mode. After a command whose wait timed out, the
command is still running; the next line typed is refused with this message
instead of being sent on top of it.

### Usage

```bash
# Single command
python mdi_command.py "G0 X10 Y10"

# Wait up to five minutes for a long move (default: 60 seconds)
python mdi_command.py --wait-timeout 300 "G1 X200 F50"

# Send without waiting
python mdi_command.py --no-wait "M5"

# Interactive mode
python mdi_command.py --interactive
```

`--wait-timeout` must be a number of seconds greater than 0 and at most 86400
(one day). It is checked before anything is sent: 0 would make the server use
its own 5 second default, and a negative value would give the wait a deadline
that has already passed.

The exit code is non-zero when the command was refused, failed, was still
running when the wait ran out, was stopped by an E-stop or power-off, or
could not be confirmed as finished in the status. In interactive mode a command that fails does
not end the session: the error is printed and the prompt comes back. The
failure is remembered, and the exit code after `quit` or end of input is
non-zero if any command in the session failed. An error while reading the
input (as opposed to its end) stops the session with a non-zero exit code.
Ctrl+C stops the example, not the machine: a command that was already sent
keeps running. The example ends at once, also in the middle of a wait, and
sends no abort: whether to abort what the machine is doing is the operator's
decision. Python and Node.js print a line saying so and exit 130; the Go and
Rust programs are ended by the signal. In Node.js this needs a `SIGINT`
listener on the `readline` interface as well as on the process: in a terminal
`readline` takes Ctrl+C itself and, without a listener, only closes the
interface, which does not end a call that is already awaited.

In Go, flags must come before the G-code:
`go run ./cmd/mdi_command --no-wait "M5"`.

### Interactive session

```
MDI> G0 X10 Y10
Executing: G0 X10 Y10
  Waiting for completion...
  Done.
MDI> G1 X20 F100
Executing: G1 X20 F100
  Waiting for completion...
  Done.
MDI> status
Position: X=20.0000 Y=10.0000 Z=0.0000
MDI> quit
```

---

## hal_query

Query HAL pins, signals, and parameters.

### What it demonstrates

- Using HalService
- Querying with glob patterns
- Different query types (pins, signals, params, components)
- Checking `success` in the response and exiting non-zero when it is false
- Watching values for changes
- Formatting HAL values

### Key code (Python)

```python
RPC_TIMEOUT = 5.0  # seconds, for every query

# Create HAL service stub
stub = hal_pb2_grpc.HalServiceStub(channel)

# Query pins matching pattern, with a deadline
request = hal_pb2.QueryPinsCommand()
request.pattern = "axis.*"
response = stub.QueryPins(request, timeout=RPC_TIMEOUT)

# The RPC can succeed while the query failed: check the flag.
if not response.success:
    print(f"Error: {response.error}", file=sys.stderr)
    sys.exit(1)

for pin in sorted(response.pins, key=lambda p: p.name):
    print(f"{pin.name:<50} {format_type(pin.type):<6} {format_value(pin.value)}")
```

### Usage

```bash
# Query all axis pins
python hal_query.py pins "axis.*"

# Query spindle signals
python hal_query.py signals "spindle*"

# Query all motion parameters
python hal_query.py params "motion.*"

# List all components
python hal_query.py components

# Print a line whenever one of these values changes (Ctrl+C to stop)
python hal_query.py watch spindle.0.speed-out axis.x.pos-cmd

# The same, checking every 2 seconds instead of every 500 ms (1 to 60000)
python hal_query.py watch spindle.0.speed-out --interval 2000

# Overall counts
python hal_query.py status
```

### Output

```
Found 2 pins matching 'axis.x.*':

Name                                               Type   Dir  Value           Signal
------------------------------------------------------------------------------------------
axis.x.pos-cmd                                     FLOAT  OUT  10.5            x-pos-cmd
axis.x.pos-fb                                      FLOAT  IN   10.4998         -
```

In the Node.js example, `s64` and `u64` values are exact only up to
`Number.MAX_SAFE_INTEGER` (2^53 - 1); the generated TypeScript decodes 64-bit
integers into JavaScript numbers, and a larger value arrives as the nearest
number.

---

## upload_file

Upload, list, and delete G-code files on the LinuxCNC machine.

### What it demonstrates

- Using `UploadFile` with `fail_if_exists`, so an existing file is never replaced
- Using `ListFiles` to browse the nc_files directory
- Using `DeleteFile` to clean up, and deleting only the file this run uploaded
- File management error handling

### Key code (Python)

```python
RPC_TIMEOUT = 10.0  # seconds, for every RPC in this example

# filename and cleanup come from --filename and --cleanup; SAMPLE_GCODE is a
# short program defined at the top of the file.

# True only once this run has created the file, so that --cleanup can
# never delete a file that was there before.
file_was_created = False

# fail_if_exists makes the server refuse to replace an existing file.
upload_request = linuxcnc_pb2.UploadFileRequest(
    filename=filename,
    content=SAMPLE_GCODE,
    fail_if_exists=True,
)
try:
    upload_response = stub.UploadFile(upload_request, timeout=RPC_TIMEOUT)
except grpc.RpcError as rpc_error:
    if rpc_error.code() == grpc.StatusCode.ALREADY_EXISTS:
        print(f"'{filename}' already exists on the machine and was left "
              "untouched. Delete it there or pick another name with "
              "--filename.", file=sys.stderr)
        sys.exit(1)
    raise
file_was_created = True
print(f"  Written to: {upload_response.path}")

# List files
list_response = stub.ListFiles(linuxcnc_pb2.ListFilesRequest(), timeout=RPC_TIMEOUT)
for file_info in list_response.files:
    print(f"{file_info.name}: {file_info.size_bytes} bytes")

# Delete only what this run created
if cleanup and file_was_created:
    stub.DeleteFile(linuxcnc_pb2.DeleteFileRequest(filename=filename), timeout=RPC_TIMEOUT)
```

Two more file RPCs are not in the example programs yet. `ReadFile` downloads
a file in chunks of up to 1 MiB, and `ResolveFile` says whether a name is the
program LinuxCNC has loaded, so you can avoid replacing or deleting a running
program:

```python
# Read a file back (loop until end_of_file for files over 1 MiB)
chunk = stub.ReadFile(linuxcnc_pb2.ReadFileRequest(filename="my_part.ngc"), timeout=10.0)
print(chunk.content.decode())

# Only delete it if it is not the program being run
resolved = stub.ResolveFile(linuxcnc_pb2.ResolveFileRequest(filename="my_part.ngc"), timeout=10.0)
if not (resolved.is_loaded_program
        and resolved.interp_state != linuxcnc_pb2.INTERP_IDLE):
    stub.DeleteFile(linuxcnc_pb2.DeleteFileRequest(filename="my_part.ngc"), timeout=10.0)
```

See [ReadFile](api-reference.md#readfile) and
[ResolveFile](api-reference.md#resolvefile).

### Usage

```bash
# Upload a sample file as grpc_example.ngc
python upload_file.py --host localhost --port 50051

# Upload under another name
python upload_file.py --filename my_test.ngc

# Upload and then delete (cleanup)
python upload_file.py --cleanup
```

A second run without `--cleanup` stops with "already exists": the first run's
file is still there and is not overwritten.

### Output

```
Uploading 'grpc_example.ngc'...
  Written to: /home/linuxcnc/linuxcnc/nc_files/grpc_example.ngc
  Size: 141 bytes

Listing files...
  Directory: /home/linuxcnc/linuxcnc/nc_files
  Name                               Size  Type
  ------------------------------ --------  ----
  grpc_example.ngc                    141  FILE
  other_program.ngc                   523  FILE
  projects                           4096  DIR
```

---

## Testing with Mock Server

For development without a real LinuxCNC installation, use the mock server:

```bash
# Start mock server
python tests/mock_server.py --port 50051

# Run examples against it
python examples/python/get_status.py --port 50051
python examples/python/stream_status.py --port 50051
python examples/python/hal_query.py --port 50051 status
```

The mock server simulates:
- All status fields with realistic values
- Command responses
- Status streaming
- HAL system status

Its status is fixed: it accepts every command but nothing changes. So
`jog_axis` and `mdi_command` stop with "did not show in the status" when they
check that their mode change took effect, which is the check doing its job.
The mock does not implement `UploadFile`, `ListFiles` or `DeleteFile`, so
`upload_file` gets `UNIMPLEMENTED`. Those three need a real server; the LinuxCNC simulator is
enough (see [E2E testing](e2e-testing.md)).

---

## Common Patterns

### Client Wrapper Class

Many examples use a client wrapper for cleaner code. Every call carries a
deadline. This is the one in `jog_axis.py`; `send_command` takes an optional
`timeout` so that the jog stop can have its own shorter deadline
(`self.send_command(command, timeout=JOG_STOP_TIMEOUT)`):

```python
RPC_TIMEOUT = 5.0  # seconds; GetStatus and SendCommand

class LinuxCNCClient:
    def __init__(self, host, port):
        self.channel = grpc.insecure_channel(f"{host}:{port}")
        self.stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(self.channel)
        self._serial = 0

    def _next_serial(self):
        self._serial += 1
        return self._serial

    def get_status(self):
        return self.stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=RPC_TIMEOUT)

    def send_command(self, command, timeout=RPC_TIMEOUT):
        """Send a command. RCS_DONE in the response means dispatched, not finished."""
        command.serial = self._next_serial()
        command.timestamp = int(time.time() * 1e9)
        return self.stub.SendCommand(command, timeout=timeout)

    def set_mode(self, mode):
        """Set task mode (MANUAL, AUTO, MDI)."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.mode.mode = mode
        return self.send_command(command)

    def set_teleop(self, enable):
        """Turn teleop on (axis jogs) or off (joint jogs, homing)."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.teleop.enable = enable
        return self.send_command(command)
```

The wrapper goes on with `wait_complete`, `jog_continuous` and `jog_stop`,
shown in the [jog_axis](#jog_axis) section. The wrapper in `mdi_command.py`
is the same except that it has no jog methods, so its `send_command` has no
`timeout` parameter, and that it has `mdi`:

```python
    def mdi(self, gcode):
        """Send an MDI command."""
        command = linuxcnc_pb2.LinuxCNCCommand()
        command.mdi.command = gcode
        return self.send_command(command)
```

`command.serial` is only a label that the server echoes back. To follow a
command, use `linuxcnc_serial` from the response, as `mdi_command` does.

### Deadlines

Every unary RPC in the examples has a deadline, so a server that stops
answering cannot hang the program. A `WaitComplete` call needs a client
deadline longer than the timeout it asks the server for, or the call is cut
off before the server can answer `RCS_EXEC`:

```python
WAIT_DEADLINE_MARGIN = 5.0

request = linuxcnc_pb2.WaitCompleteRequest(timeout=wait_timeout)
finished = stub.WaitComplete(request, timeout=wait_timeout + WAIT_DEADLINE_MARGIN)
```

### Error Handling

```python
try:
    status = stub.GetStatus(request, timeout=5.0)
except grpc.RpcError as e:
    if e.code() == grpc.StatusCode.UNAVAILABLE:
        print("Server not available")
    elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        print("Request timed out")
    else:
        print(f"Error: {e.code()}: {e.details()}")
```

### Streaming Timeouts

A stream that runs until Ctrl+C cannot have an overall deadline. The
`stream_status` examples instead give up, and exit non-zero, when no update
has arrived for too long. How long depends on the interval that was asked
for: 30 seconds, or twice the interval plus 5 seconds if that is longer. With
the default 100 ms interval that is 30 seconds; with `--interval 40000` it is
85 seconds, so a slow stream is not mistaken for a dead one. In Python:

```python
MIN_STREAM_IDLE_TIMEOUT = 30.0   # seconds
STREAM_IDLE_INTERVALS = 2
STREAM_IDLE_MARGIN = 5.0         # seconds

def stream_idle_timeout(interval_ms):
    """Seconds without an update after which the stream counts as dead."""
    return max(MIN_STREAM_IDLE_TIMEOUT,
               STREAM_IDLE_INTERVALS * interval_ms / 1000.0 + STREAM_IDLE_MARGIN)
```

In Rust each `stream.next()` call is wrapped with `tokio::time::timeout`. So
is the call that opens the stream: it returns when the server's response
headers arrive, and a server may send those only together with the first
update, so without a timeout a server that accepts the connection and then
never answers would hang the program:

```rust
use tokio::time::{timeout, Duration};

const MIN_STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(30);
const STREAM_IDLE_INTERVALS: u32 = 2;
const STREAM_IDLE_MARGIN: Duration = Duration::from_secs(5);

fn stream_idle_timeout(interval_ms: i32) -> Duration {
    let update_interval = Duration::from_millis(interval_ms.max(0) as u64);
    MIN_STREAM_IDLE_TIMEOUT.max(update_interval * STREAM_IDLE_INTERVALS + STREAM_IDLE_MARGIN)
}

let idle_timeout = stream_idle_timeout(args.interval);

// Opening the stream waits for the response headers.
match timeout(idle_timeout, client.stream_status(request)).await {
    Ok(Ok(response)) => { /* opened: let mut stream = response.into_inner() */ }
    Ok(Err(open_error)) => { /* gRPC error */ }
    Err(_) => { /* no answer in time: give up */ }
}

// Then, for every update:
match timeout(idle_timeout, stream.next()).await {
    Ok(Some(Ok(status))) => { /* process */ }
    Ok(Some(Err(stream_error))) => { /* gRPC error, including a stream the server cancelled */ }
    Ok(None) => { /* stream ended */ }
    Err(_) => { /* no update in time: give up */ }
}
```

In the example both awaits also sit in a `tokio::select!` with Ctrl+C. Go
resets a `time.AfterFunc` timer on every update, Node.js refreshes a
`setTimeout`, and Python runs a small watchdog thread; each cancels the stream
when the time is up.

A stream that ends with the status `CANCELLED` is a clean end only when the
example cancelled it itself, which it does on Ctrl+C (exit code 0) and when
the idle time is up (reported, exit code 1). If the server cancels the
stream, the example reports the status and exits non-zero like for any other
stream error. Each example therefore keeps its own record of having
cancelled, and does not go by the status code alone: Go looks at its context,
Node.js sets a flag before it calls `stream.cancel()`, Python sets the
watchdog's flag (Ctrl+C arrives there as `KeyboardInterrupt`, not as a
status), and Rust never cancels through the stream at all.

`hal_query watch` has no idle timeout: the server only sends something when a
watched value changes, so a quiet stream is normal there. It treats
`CANCELLED` the same way, and its Rust version also watches for Ctrl+C while
the watch is being opened.

### Stopping Motion on Ctrl+C

A program that starts motion must stop it on the way out, whatever the reason
for leaving. In `jog_axis` the signal handler only sets a flag; the waits
check the flag and raise, and the jog stop sits in a `finally`:

```python
import signal
import time

INTERRUPT_CHECK_INTERVAL = 0.02     # how often a sleep looks at the flag
interrupt_was_requested = False     # a plain boolean, not a threading.Event

class Interrupted(Exception):
    """Ctrl+C or SIGTERM arrived."""

def install_interrupt_handlers():
    def remember_interrupt(signal_number, frame):
        # No lock, no output, no exception: only the flag.
        global interrupt_was_requested
        interrupt_was_requested = True

    signal.signal(signal.SIGINT, remember_interrupt)
    signal.signal(signal.SIGTERM, remember_interrupt)

def pause(seconds):
    """Sleep in short slices; raise Interrupted soon after an interrupt arrives."""
    wake_time = time.monotonic() + seconds
    while True:
        if interrupt_was_requested:
            raise Interrupted()
        remaining = wake_time - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, INTERRUPT_CHECK_INTERVAL))

install_interrupt_handlers()        # before anything moves
try:
    client.jog_continuous(axis, velocity)
    pause(0.5)
finally:
    stop_jog(client, axis)          # own short deadline; raises if it fails
```

`client.jog_continuous` and `stop_jog` are the ones shown in the
[jog_axis](#jog_axis) section. The real code also checks the response of
`jog_continuous`.

The continuous jog's stop is sent on every way out. The incremental jog is
different in one case only: when it completed normally and the status showed
that the axis moved and is standing still, there is nothing left to stop and
no stop is sent. If it is interrupted or fails part way, the stop goes out.

If the stop itself fails, the example says that the axis may still be moving
and to use the E-stop, and exits 1 in all four languages, also when Ctrl+C
(which would otherwise give 130) was what led to the stop.

Three things can get between a running jog and its stop, and the examples
avoid each:

- **Exiting from inside the handler** (`os._exit` in Python, `process.exit` in
  Node.js, `os.Exit` or `log.Fatal` in Go) skips the stop.
- **A handler that takes a lock.** A Python signal handler runs in the main
  thread, between two instructions of whatever was running. `Event.set()` and
  `Event.wait()` share a lock, so a handler that calls `set()` while the main
  thread is inside `wait()` blocks forever. That is why the flag is a plain
  boolean and the wait is a loop of short sleeps. Go (`signal.NotifyContext`),
  Node.js (handlers run on the event loop) and Rust (a tokio task sets an
  atomic flag) do not run code inside the signal handler.
- **Output that fails.** If the example's output goes to a pipe and the reader
  closes it, Python's `print` raises `BrokenPipeError`, Rust's `println!`
  panics, Go exits on `SIGPIPE` and Node.js ends on the stream's unhandled
  `error` event. So nothing is printed between the start of a jog and its
  stop, and from the start of the first jog onwards a failed write is ignored:
  Python prints through a function that catches the error, Rust writes with
  `let _ = writeln!(...)`, Go calls `signal.Ignore(syscall.SIGPIPE)` and
  Node.js adds `error` listeners to `process.stdout` and `process.stderr`.

An exception in the jog code leaves through the same stop (`finally` or
`except BaseException` in Python, `try`/`finally` in Node.js), and so does a
panic: Go recovers, sends the stop and panics again, and Rust catches the
unwind, sends the stop and resumes it. If that stop fails, both print the
failure to standard error before the panic goes on, because the panic would
otherwise be the only thing reported.

A signal that the example does not handle (`SIGHUP` when the terminal closes,
`SIGKILL`) still ends it without a stop, as does losing the network. Keep the
E-stop in reach.
