---
title: "API Reference"
weight: 40
---

Complete API documentation for LinuxCNC gRPC services.

## Table of Contents

- [LinuxCNCService](#linuxcncservice)
  - [GetStatus](#getstatus)
  - [SendCommand](#sendcommand)
  - [WaitComplete](#waitcomplete)
  - [StreamStatus](#streamstatus)
  - [StreamErrors](#streamerrors)
  - [UploadFile](#uploadfile)
  - [ListFiles](#listfiles)
  - [DeleteFile](#deletefile)
  - [ReadFile](#readfile)
  - [ResolveFile](#resolvefile)
  - [GetMachineConfig](#getmachineconfig)
  - [GetServerInfo](#getserverinfo)
- [HalService](#halservice)
  - [GetSystemStatus](#getsystemstatus)
  - [SendCommand](#sendcommand-hal)
  - [GetValue](#getvalue)
  - [QueryPins](#querypins)
  - [QuerySignals](#querysignals)
  - [QueryParams](#queryparams)
  - [QueryComponents](#querycomponents)
  - [StreamStatus](#streamstatus-hal)
  - [WatchValues](#watchvalues)
- [Enums](#enums)
- [Messages](#messages)

---

## LinuxCNCService

Main service for machine control and status monitoring.

### GetStatus

Get the current machine status.

```protobuf
rpc GetStatus(GetStatusRequest) returns (LinuxCNCStatus)
```

**Request:** `GetStatusRequest` (empty message)

**Response:** `LinuxCNCStatus` containing:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | int64 | Unix timestamp in nanoseconds |
| `version` | string | LinuxCNC version |
| `task` | TaskStatus | Task/interpreter state |
| `trajectory` | TrajectoryStatus | Motion planning state |
| `position` | PositionStatus | All position information |
| `joints` | JointStatus[] | Per-joint status, one entry for each joint the machine has (`trajectory.joints` of them), in joint order |
| `axes` | AxisStatus[] | Per-axis status (up to 9: XYZABCUVW) |
| `spindles` | SpindleStatus[] | Per-spindle status, one entry for each spindle the machine has (`trajectory.spindles` of them) |
| `tool` | ToolStatus | Tool table and current tool. `tool_table` leaves out empty slots; see [ToolEntry](#toolentry) for how to find the tool `pocket_prepped` refers to |
| `io` | IOStatus | Digital/analog I/O state |
| `gcode` | GCodeStatus | Active G-codes and M-codes |
| `limits` | LimitStatus | Limit switch states. `limit_flags` and `homed` have one entry for each joint the machine has. `misc_error` carries LinuxCNC's misc error inputs; its length is that of LinuxCNC's own array, not the joint count, and it is empty on LinuxCNC 2.8 |
| `errors` | ErrorMessage[] | The most recent messages the server has read from LinuxCNC's error channel (at most 20, oldest first). Empty unless the server reads the channel: see [StreamErrors](#streamerrors) |

LinuxCNC's own status always has 16 joint entries and 8 spindle entries,
whatever the machine has. The server reports only the real ones, so `joints`,
`spindles`, `limits.limit_flags` and `limits.homed` can be used as they come.
A 1.0 server sent all 16 and 8. A client that also talks to one should look
only at the first `trajectory.joints` joints and `trajectory.spindles`
spindles, which gives the same result with either server.

**Error codes:**

| Code | Condition |
|------|-----------|
| `UNAVAILABLE` | The server is not connected to LinuxCNC, or LinuxCNC could not be read |
| `INTERNAL` | Reading or mapping LinuxCNC's status failed in a way the server does not expect. The message names the error |

**Example (Python):**

```python
status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
print(f"Mode: {linuxcnc_pb2.TaskMode.Name(status.task.task_mode)}")
print(f"X: {status.position.actual_position.x}")
```

---

### SendCommand

Send a command to LinuxCNC.

```protobuf
rpc SendCommand(LinuxCNCCommand) returns (CommandResponse)
```

**Request:** `LinuxCNCCommand` with one of:

| Command Field | Type | Description |
|---------------|------|-------------|
| `state` | StateCommand | Set machine state (ESTOP, ON, OFF) |
| `mode` | ModeCommand | Set mode (MANUAL, AUTO, MDI) |
| `mdi` | MdiCommand | Execute G-code via MDI |
| `jog` | JogCommand | Jog axis/joint |
| `home` | HomeCommand | Home joint(s) |
| `unhome` | UnhomeCommand | Unhome joint |
| `spindle` | SpindleControlCommand | Spindle control |
| `spindle_override` | SpindleOverrideCommand | Set spindle override |
| `brake` | BrakeCommand | Spindle brake control |
| `feedrate` | FeedrateCommand | Set feed rate override |
| `rapidrate` | RapidrateCommand | Set rapid rate override |
| `coolant` | CoolantCommand | Mist/flood control |
| `program` | ProgramCommand | Program control (open, run, pause, etc.) |
| `digital_output` | DigitalOutputCommand | Set digital output |
| `analog_output` | AnalogOutputCommand | Set analog output |
| `override_limits` | OverrideLimitsCommand | Override limit switches |
| `program_options` | ProgramOptionsCommand | Set optional stop/block delete |

> **`AutoCommand` is deprecated.** The message is in the proto, but
> `LinuxCNCCommand` has no field for it, so it cannot be sent. Use
> `ProgramCommand` (`open`, `run_from_line`, `pause`, `resume`, `step`,
> `abort`) instead. `AutoCommand` and `AutoCommandType` will be removed in 2.0.

**Response:** `CommandResponse`

| Field | Type | Description |
|-------|------|-------------|
| `serial` | int32 | The request's `serial`, or one from the server's own counter if the request left it at 0 (the counter wraps to 1 after 2147483647). A label only: it is not LinuxCNC's serial (don't compare it with `echo_serial_number`) and `WaitComplete` ignores it |
| `status` | RcsStatus | `RCS_DONE` if the command was dispatched. `RCS_ERROR` if the server rejected it, a LinuxCNC call failed or LinuxCNC did not take the command; a command made of several LinuxCNC messages may then be partly sent (see [`RCS_ERROR` from `SendCommand`](server.md#rcs_error-from-sendcommand)) |
| `error_message` | string | Error details if status is RCS_ERROR |
| `linuxcnc_serial` | optional int32 | LinuxCNC's own serial for the command. Set when the command was dispatched. It identifies the command LinuxCNC was given; it is not proof that the task received it or finished it. Pass it to [WaitComplete](#waitcomplete), or compare it with `task.echo_serial_number` in the status, to follow the command and confirm it (see [Command and Error Semantics](server.md#linuxcnc_serial-identifies-the-command-to-linuxcnc)). For a command sent as several LinuxCNC messages it is the serial of the last one LinuxCNC took. Unset whenever `status` is `RCS_ERROR` (also when part of the command was sent), or if the LinuxCNC bindings on the server don't expose the serial (they do in 2.8, 2.9 and master) |

**Example (Python):**

```python
# Set state to ON
cmd = linuxcnc_pb2.LinuxCNCCommand()
cmd.serial = 1
cmd.state.state = linuxcnc_pb2.STATE_ON
response = stub.SendCommand(cmd)

if response.status == linuxcnc_pb2.RCS_ERROR:
    print(f"Error: {response.error_message}")
```

> **`RCS_DONE` from `SendCommand` means "dispatched", not "finished".** The
> command has been handed to LinuxCNC and may still be executing. Use
> [WaitComplete](#waitcomplete) or watch the status to find out when it is
> done. See [Command and Error Semantics](server.md#command-and-error-semantics).

The server sends one command at a time. While it shuts down, commands still
waiting their turn are answered `RCS_ERROR` without being sent, except those
that only stop something (E-stop, machine off, program abort and pause, jog
stop, spindle off), which are always sent. The same applies to a command
whose client has gone away before its turn.

#### Values the server rejects

The server checks a command before it sends anything to LinuxCNC. A command
that fails a check gets `RCS_ERROR` with the reason in `error_message`, has no
`linuxcnc_serial`, and nothing is sent.

- **Numbers must be finite** (not NaN, not infinite): the jog `velocity`
  (continuous and incremental jogs) and `increment` (incremental jogs), the
  spindle `speed` (forward and reverse), the feed, rapid and spindle override
  `scale`, the maximum velocity, the tool offset values (`z_offset`,
  `x_offset`, `diameter`, `front_angle`, `back_angle`), the analog output
  `value` and both `set_limit` limits.
- **These must also be zero or more:** the spindle `speed` for forward and
  reverse, the three override scales and the maximum velocity. A jog
  `velocity` may be negative: its sign is the jog direction. It is in machine
  units per second.
- **`set_limit`** needs `min_limit` below `max_limit`. Otherwise neither limit
  is sent.
- **`brake`** takes `BRAKE_ENGAGE` or `BRAKE_RELEASE`. Any other value is
  rejected; it is not taken as a release.
- **Joint, axis and spindle numbers** in jog, home, unhome, spindle, spindle
  override, brake, `set_limit` and `override_config` commands must exist on
  the machine. Joints are counted as the machine has them
  (`trajectory.joints`) and spindles likewise (`trajectory.spindles`), which is
  also how many entries `joints` and `spindles` have in the status. A
  negative number is rejected, except `-1` for home and unhome, which means
  all joints. A command made of several messages (`set_limit`,
  `override_config`) is checked completely before the first one is sent.
- Only the fields the requested operation uses are checked. A jog stop is
  never refused because of its `velocity`, nor a spindle stop because of its
  `speed`.

#### Spindle commands

`SpindleControlCommand` carries a `command`, the `spindle` number (default 0),
and for forward and reverse a `speed` and `wait_for_at_speed`. The `spindle`
number is used by every command.

| `command` | Effect |
|-----------|--------|
| `SPINDLE_CMD_FORWARD`, `SPINDLE_CMD_REVERSE` | Turn the spindle on at `speed`. The direction comes from the command, so `speed` must be zero or more; a negative speed is rejected. `wait_for_at_speed` is passed to LinuxCNC when true |
| `SPINDLE_CMD_OFF` | Stop the spindle |
| `SPINDLE_CMD_INCREASE`, `SPINDLE_CMD_DECREASE` | LinuxCNC's `SPINDLE_INCREASE` and `SPINDLE_DECREASE` |
| `SPINDLE_CMD_CONSTANT` | LinuxCNC's `SPINDLE_CONSTANT` |

#### Modes and teleop: what to send first

**The server never switches the task mode or teleop for you.** Every command is
passed to LinuxCNC as it is, and LinuxCNC refuses a command sent in the wrong
mode. The table gives what each kind of command needs. It was measured on the
LinuxCNC 2.9 simulator (`sim/axis`, trivkins); the error texts are LinuxCNC's.

| Command | Task mode | Teleop (`trajectory.motion_mode`) | In the wrong state |
|---------|-----------|-----------------------------------|--------------------|
| `home`, `unhome` | `MODE_MANUAL` | Off (`TRAJ_MODE_FREE`) | "must be in joint mode to home" |
| `jog` with `is_joint = true` | `MODE_MANUAL` | Off (`TRAJ_MODE_FREE`) | "Mode is TELEOP, cannot jog joint" |
| `jog` with `is_joint = false` (axis jog) | `MODE_MANUAL` | On (`TRAJ_MODE_TELEOP`) | "Joint jog requested for undefined joint number=-1" |
| `mdi` | `MODE_MDI`, joints homed | Set by LinuxCNC (`TRAJ_MODE_COORD`) | "Can't issue MDI command when not homed", or a mode error |
| `program` (`open`, `run_from_line`, …) | `MODE_AUTO` | Set by LinuxCNC | A mode error |

How to sequence commands safely:

1. **Send the mode (or teleop) command, then wait until status shows it.**
   A mode change takes effect a moment after `SendCommand` returns. Either
   call `WaitComplete` with the `linuxcnc_serial` from the response, or poll
   `GetStatus` until `task.task_mode` (or `trajectory.motion_mode`) has the
   value you asked for. Don't sleep for a fixed time.
2. **Joints can be jogged before homing; axes cannot.** A joint jog works on
   an unhomed machine. An axis jog needs teleop, which needs the joints
   homed. To move an unhomed machine, jog joints (use
   [GetMachineConfig](#getmachineconfig) to find the joint for an axis).
3. **Homing changes teleop.** On the simulator, LinuxCNC switched to teleop by
   itself once every joint was homed. So after homing, a joint jog or a second
   `home` is refused until you send `teleop` with `enable = false`.
4. **MDI and auto change it too.** Entering `MODE_MDI` put the motion mode at
   `TRAJ_MODE_COORD`; going back to `MODE_MANUAL` did not always restore
   teleop. Check `trajectory.motion_mode` before an axis jog rather than
   assume, and send `teleop` if needed.

How refusals show up:

- **A jog or home that LinuxCNC refuses still returns `RCS_DONE` from
  `SendCommand`, and `WaitComplete` reports `RCS_DONE` for it as well.** The
  refusal comes from the motion controller as an operator error message, not
  as a failed command. You see it only as an error message
  ([StreamErrors](#streamerrors) or `status.errors`) and in the fact that
  nothing moves or homes.
- **An MDI command in the wrong mode** also returns `RCS_DONE` from
  `SendCommand`, but `WaitComplete` with its `linuxcnc_serial` returns
  `RCS_ERROR`.
- So after any motion command, confirm the effect in the status (position,
  `homed`, `motion_mode`) instead of trusting the response alone.

Telling whether a jog is moving:

- **`trajectory.inpos` stays `true` during an axis jog**, and so does the
  joint's `inpos`. Don't use it to wait for a jog to finish.
- Use `trajectory.current_vel`, `axes[n].velocity` or `joints[n].velocity`:
  they are non-zero while the jog moves and return to zero when it stops.
- **After an E-stop `trajectory.current_vel` keeps its last value.** An
  E-stop during a jog stops the axis at once, but on the 2.9 simulator the
  velocity in the status stayed at its last non-zero value until the machine
  was turned on again. A loop that waits for the velocity to reach zero must
  also look at `task.task_state` and stop waiting when it is no longer
  `STATE_ON`.

Telling whether an MDI move is running:

- **`task.interp_state` alone is not a reliable busy test for MDI.** Measured
  on the 2.9 simulator: in 7 of 71 six-second MDI moves it read `INTERP_IDLE`
  for the whole move, and `task.exec_state` read `EXEC_DONE` in the middle
  of a move.
- **`trajectory.inpos` was `false` in every reading taken during an MDI move
  and `true` at its end.** It was the only field that reliably told a
  running MDI move apart.
- So treat the machine as idle only when all three agree:
  `task.interp_state` is `INTERP_IDLE`, `trajectory.inpos` is `true` and
  `trajectory.current_vel` is zero. `inpos` covers MDI and program moves,
  the velocity covers jogs, and the interpreter state covers a program that
  is running but not moving (a dwell, a pause).

```python
def set_mode_and_wait(stub, mode, timeout=5.0):
    sent = stub.SendCommand(
        linuxcnc_pb2.LinuxCNCCommand(mode=linuxcnc_pb2.ModeCommand(mode=mode))
    )
    request = linuxcnc_pb2.WaitCompleteRequest(timeout=timeout)
    if sent.HasField("linuxcnc_serial"):
        request.linuxcnc_serial = sent.linuxcnc_serial
    stub.WaitComplete(request)
    status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
    if status.task.task_mode != mode:
        raise RuntimeError("mode change did not take effect")
```

---

### WaitComplete

Wait for a command to complete execution.

```protobuf
rpc WaitComplete(WaitCompleteRequest) returns (CommandResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `serial` | int32 | Ignored. The wait is always on the most recent command sent through this server; the value is only echoed back in the response |
| `timeout` | double | Timeout in seconds. 0, a negative number or NaN means the default, 5 seconds. An infinite timeout waits until the command finishes, the client goes away or the server shuts down |
| `linuxcnc_serial` | optional int32 | Wait on this serial: the `linuxcnc_serial` that `SendCommand` returned for a command. It must be a serial this server issued on its current connection to LinuxCNC; any other value gets `RCS_ERROR`. When unset, the wait is on the most recent command sent through this server |

**Response:** `CommandResponse`

| Status | Meaning |
|--------|---------|
| `RCS_DONE` | The command finished |
| `RCS_ERROR` | The command failed, or LinuxCNC could not be read (`error_message` then carries the error) |
| `RCS_EXEC` | The timeout expired and the command is still running |

The server polls status while it waits and does not block other RPCs, so a
command such as an estop can be sent while a `WaitComplete` is in progress.
The timeout also covers the time the call itself spends waiting for the
server's internal lock: if another RPC holds the lock past the deadline, the
answer is `RCS_EXEC`, as for any timeout.

If LinuxCNC has already moved on to a later command, or the server has no
command to wait for (nothing sent yet, or it reconnected to LinuxCNC), the
result is `RCS_DONE` immediately. A wait that names a `linuxcnc_serial` is
stricter in one respect: after a reconnect it returns `RCS_ERROR`, not
`RCS_DONE`.

A wait with `linuxcnc_serial` follows LinuxCNC's own serial rule for that
command. It is exact as long as nothing else is sent to LinuxCNC meanwhile.
Once a later command has been echoed, sent through this server or by any
other client of the same LinuxCNC, the answer is `RCS_DONE` and the outcome
of the earlier command is not known from the wait. An E-stop or an abort
from another client is such a later command: it also ends the wait with
`RCS_DONE`, while the move was cut short. Check `task.task_state` for the
E-stop; after an abort the machine is on and idle as after a finished move,
and only the position (short of the target) shows it. A client that needs to
know a motion has finished should also read the status (`task.interp_state`,
`trajectory.inpos` and `trajectory.current_vel` together; see
[Modes and teleop](#modes-and-teleop-what-to-send-first) for why the
interpreter state alone is not enough). See
[Command and Error Semantics](server.md#waitcomplete) for the rules in full.

**Example (Python):**

```python
# Send MDI command
cmd = linuxcnc_pb2.LinuxCNCCommand()
cmd.serial = 1
cmd.mdi.command = "G0 X10 Y10"
sent = stub.SendCommand(cmd)
if sent.status != linuxcnc_pb2.RCS_DONE:
    raise SystemExit(f"Not sent: {sent.error_message}")

# Wait on this command's serial. linuxcnc_serial is optional: only pass
# it on if the server set it, or the request would name serial 0.
request = linuxcnc_pb2.WaitCompleteRequest(timeout=30.0)
if sent.HasField("linuxcnc_serial"):
    request.linuxcnc_serial = sent.linuxcnc_serial
response = stub.WaitComplete(request)

if response.status == linuxcnc_pb2.RCS_DONE:
    # Finished, unless LinuxCNC took a later command from any client
    # meanwhile: to be sure, also read the status, as described above.
    print("Command completed")
```

Without `linuxcnc_serial`, `WaitComplete` reports on whatever command was sent
through the server last, which may be another client's.

---

### StreamStatus

Subscribe to real-time status updates.

```protobuf
rpc StreamStatus(StreamStatusRequest) returns (stream LinuxCNCStatus)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `interval_ms` | int32 | Update interval in milliseconds. 0 or less means the default, 100. Values above 60000 (60 seconds) are treated as 60000 |

**Response:** Stream of `LinuxCNCStatus` messages

The stream ends as soon as the client cancels or disconnects; the server does
not wait for the current interval to pass. It ends with `UNAVAILABLE` when
LinuxCNC can no longer be read, and with `INTERNAL` (after the statuses
already sent) when reading or mapping the status fails in a way the server
does not expect; the message names the error.

**Example (Python):**

```python
request = linuxcnc_pb2.StreamStatusRequest(interval_ms=100)

for status in stub.StreamStatus(request):
    pos = status.position.actual_position
    print(f"X={pos.x:.3f} Y={pos.y:.3f} Z={pos.z:.3f}")
```

---

### StreamErrors

Subscribe to messages from LinuxCNC's error channel: operator errors, texts
and displays, and NML errors.

```protobuf
rpc StreamErrors(StreamErrorsRequest) returns (stream ErrorMessage)
```

**Request:** `StreamErrorsRequest`

| Field | Type | Description |
|-------|------|-------------|
| `replay_buffered` | bool | If true, the stream starts with the messages the server already holds (the most recent 256), oldest first, then continues with new ones. If false (the default), only messages read from now on are sent |

**Response:** Stream of `ErrorMessage`

| Field | Type | Description |
|-------|------|-------------|
| `type` | ErrorType | OPERATOR_ERROR, NML_ERROR, etc. |
| `message` | string | Error message text |
| `timestamp` | int64 | When the server read the message, unix timestamp in nanoseconds |
| `sequence` | int64 | Position in the order the server read messages, starting at 1. The same message has the same `sequence` here and in `LinuxCNCStatus.errors` |

- **Every subscriber receives every message the server reads, as long as it
  keeps up.** The server reads the channel in one place and fans the messages
  out, so several clients can stream at once. (Up to 1.0.0, concurrent streams
  split the messages between them.) The server keeps the newest 256 messages;
  a subscriber that falls further behind than that loses the older ones. The
  stream does not report an error when that happens.
- **The response headers are sent as soon as the subscription is in place.** A
  client that waits for them knows that any message read after that point
  will reach it.
- **Use `sequence` to drop duplicates and spot gaps.** A message can reach you
  through the stream, through `status.errors` and through a replay. On a
  stream, a jump in `sequence` means your client fell more than 256 messages
  behind and lost the ones in between; that jump is the only sign (a 1.0
  client, which does not know the field, cannot tell). In `status.errors`,
  which only ever holds the newest 20, gaps between two polls are normal
  whenever more than 20 messages arrived in between. Sequences restart at 1
  when the server restarts.

> **Reading the error channel takes messages away from the machine's GUI.**
> LinuxCNC's error channel is a single queue with no way to look without
> taking: a message the server reads does not appear in AXIS, gmoccapy or any
> other reader, and the reverse. By default the server reads the channel only
> while at least one `StreamErrors` client is connected. The server's
> `--error-channel` option changes that; see
> [Error messages and the GUI](server.md#error-messages-and-the-gui).

**Error codes:**

| Code | Condition |
|------|-----------|
| `FAILED_PRECONDITION` | The server runs with `--error-channel off` |

**Example (Python):**

```python
request = linuxcnc_pb2.StreamErrorsRequest(replay_buffered=True)
for error in stub.StreamErrors(request):
    print(f"#{error.sequence} {error.message}")
```

---

### File Management

The following RPCs manage G-code files in the NC files directory (default: `/home/linuxcnc/linuxcnc/nc_files`, configurable via `--nc-files` flag or `LINUXCNC_NC_FILES` environment variable). See [Server Configuration](server.md#nc-files-directory) for details.

All filenames are relative to this directory. Path traversal outside it is rejected. An upload can hold at most 10 MiB (10,485,760) characters of text, and the whole request must fit in the server's 11 MiB receive limit (see [UploadFile](#uploadfile)). Downloads are read in chunks of up to 1 MiB.

### UploadFile

Upload a G-code file to the nc_files directory.

```protobuf
rpc UploadFile(UploadFileRequest) returns (UploadFileResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Relative path within nc_files dir (e.g. `"part1.ngc"` or `"subdir/part1.ngc"`) |
| `content` | string | G-code text content |
| `fail_if_exists` | bool | If true, fail when file already exists (default: false = replace it). The name is then given to the upload only if it is still free when the content is complete, so of two concurrent uploads of the same new name only one succeeds |

**Response:** `UploadFileResponse`

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Absolute path where file was written |
| `overwritten` | bool | True if an existing file was replaced |

**Error codes:**

| Code | Condition |
|------|-----------|
| `INVALID_ARGUMENT` | Empty filename, empty content, null bytes, path traversal, a symbolic link loop in the name, or content longer than 10,485,760 characters (10 MiB) |
| `ALREADY_EXISTS` | File exists and `fail_if_exists` is true |
| `INTERNAL` | The file could not be written (the message names the error). Also `fail_if_exists` on a file system without hard links |
| `RESOURCE_EXHAUSTED` | The request message is larger than the server's receive limit of 11 MiB (11,534,336 bytes). gRPC rejects it before `UploadFile` runs |

The 10 MiB limit counts characters, not bytes (`content` is a string). For plain ASCII G-code the two are the same. Text with non-ASCII characters takes more than one byte per character, so it can stay under the character limit and still exceed the receive limit.

The server writes the content to a temporary file in the destination's
directory and gives it the destination's name in one step once it is
complete. So:

- A reader never sees part of an upload, and an upload that fails (a full
  disk, for example) leaves the previous file as it was, or no file if there
  was none.
- Replacing a file puts a new file under the name. It keeps the old file's
  permission bits, but another hard link to the old file keeps the old
  content, and so does a program that has the old file open. A file the
  server has no permission to write is not replaced (`INTERNAL`).
- `overwritten` is what the server saw just before it wrote; a file that
  another client creates in between is replaced and still reported as
  `overwritten: false`. With `fail_if_exists` it is always false.
- With `fail_if_exists` the name is claimed with a hard link, which the file
  system must support. Where it does not (some network and FAT mounts) the
  upload fails with `INTERNAL` and writes nothing; upload without
  `fail_if_exists` there.
- The temporary file is named `.linuxcnc-grpc-upload-<random>.tmp`. It is
  removed whether the upload succeeds or fails; only a server that is killed
  during an upload leaves one behind, which can be deleted. `ListFiles` does
  not show names of this form.

**Example (Python):**

```python
request = linuxcnc_pb2.UploadFileRequest(
    filename="my_part.ngc",
    content="G0 X10 Y10\nG1 Z-5 F100\nM2\n"
)
response = stub.UploadFile(request)
print(f"Written to: {response.path}")
```

---

### ListFiles

List files in the nc_files directory.

```protobuf
rpc ListFiles(ListFilesRequest) returns (ListFilesResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `subdirectory` | string | Optional subdirectory relative to nc_files (empty = root) |

**Response:** `ListFilesResponse`

| Field | Type | Description |
|-------|------|-------------|
| `files` | FileInfo[] | Files in the directory |
| `directory` | string | Absolute path of listed directory |

**FileInfo:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Filename |
| `path` | string | Relative path from nc_files root |
| `size_bytes` | int64 | File size in bytes |
| `modified_timestamp` | int64 | Last modified time (unix nanos) |
| `is_directory` | bool | True if directory |

Names of the form `.linuxcnc-grpc-upload-<random>.tmp` are left out: they are
the temporary files of uploads (see [UploadFile](#uploadfile)).

**Error codes:**

| Code | Condition |
|------|-----------|
| `NOT_FOUND` | Subdirectory does not exist |
| `INVALID_ARGUMENT` | Path traversal attempt, or a symbolic link loop in the name |

**Example (Python):**

```python
response = stub.ListFiles(linuxcnc_pb2.ListFilesRequest())
for f in response.files:
    print(f"{f.name}: {f.size_bytes} bytes")
```

---

### DeleteFile

Delete a file from the nc_files directory.

```protobuf
rpc DeleteFile(DeleteFileRequest) returns (DeleteFileResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Relative path within nc_files dir |

**Response:** `DeleteFileResponse`

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Absolute path of deleted file |

**Error codes:**

| Code | Condition |
|------|-----------|
| `NOT_FOUND` | File does not exist |
| `INVALID_ARGUMENT` | Path traversal attempt, a symbolic link loop in the name, or target is a directory |

**Example (Python):**

```python
response = stub.DeleteFile(
    linuxcnc_pb2.DeleteFileRequest(filename="my_part.ngc")
)
print(f"Deleted: {response.path}")
```

> `DeleteFile` and `UploadFile` act on the file a name resolves to. If the
> name is a symbolic link to another file in the directory, the link's target
> is deleted or replaced, and the link stays. Use [ResolveFile](#resolvefile) to see what a
> name resolves to.

### ReadFile

Read a file from the nc_files directory, whole or in chunks.

```protobuf
rpc ReadFile(ReadFileRequest) returns (ReadFileResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Relative path within nc_files dir |
| `offset` | int64 | Byte offset to start reading from (default 0) |
| `max_bytes` | int32 | Most bytes to return. 0, or anything above 1 MiB (1,048,576), means 1 MiB |

**Response:** `ReadFileResponse`

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Absolute, canonical path of the file read |
| `content` | bytes | File bytes starting at `offset` |
| `offset` | int64 | The offset this content starts at |
| `total_size_bytes` | int64 | Size of the whole file when it was read |
| `end_of_file` | bool | True if this content reaches the end of the file |
| `modified_timestamp` | int64 | Last modified time (unix nanoseconds) |

One response carries at most 1 MiB, so it always fits in a gRPC client's
default 4 MB receive limit. For a larger file, call again with `offset`
advanced by the length of the content you received, until `end_of_file` is
true. Files of any size can be read this way (uploads are capped at 10 MiB,
but the directory may hold larger files put there by other means).

- `content` is raw bytes. A chunk boundary can fall inside a multi-byte UTF-8
  character, so join all the chunks before decoding.
- If `modified_timestamp` or `total_size_bytes` changes between chunks, the
  file was modified while you were reading it. Start again from offset 0, and
  check at the end that you received `total_size_bytes` bytes. This catches
  ordinary rewrites; a file rewritten with the same size and timestamp between
  two chunks cannot be told apart, so don't read a file while you know it is
  being written.
- If the file's size or modification time changes while the server is reading
  one chunk, the call fails with `ABORTED` instead of returning content that
  may mix two versions. Start again from offset 0.
- An `offset` at or past the end of the file is not an error: `content` is
  empty and `end_of_file` is true.
- Node.js/TypeScript: the client gives `modified_timestamp` in milliseconds,
  not nanoseconds (see
  [64-bit integers in Node.js/TypeScript](#64-bit-integers-in-nodejstypescript)).
  Compared between chunks, it cannot show a
  rewrite that keeps the size and falls in the same millisecond as the
  previous one. The server's own check while it reads one chunk uses the
  nanosecond value and is not affected.
- The path rules are the same as for `UploadFile` and `DeleteFile`. A symbolic
  link is followed only if its target is inside the nc_files directory.
- The file RPCs check a name and then act on it. A program running on the
  machine with write access to the nc_files directory could change the
  directory between those two steps. If the last component of the checked
  path is replaced by a symbolic link in that gap:
  - `ReadFile` opens the file without following a link there, so the call
    fails instead of reading the link's target.
  - `UploadFile` puts the finished file in place without following a link
    there: without `fail_if_exists` the link itself is replaced, with it the
    upload is refused with `ALREADY_EXISTS`.
  - `DeleteFile` removes the link itself, not the file it points to.
  - `ListFiles` is not protected: it follows a link put in place of the
    directory and lists the directory the link points to.

  None of them is protected against a change higher up the path: the server
  does not defend against a hostile local user rearranging the directory
  tree.

**Error codes:**

| Code | Condition |
|------|-----------|
| `NOT_FOUND` | File does not exist |
| `INVALID_ARGUMENT` | Path traversal attempt, a symbolic link loop in the name, the name is a directory or not a regular file (a FIFO or device is refused without being read), or `offset` or `max_bytes` is negative |
| `ABORTED` | The file's size or modification time changed during the read. Start again from offset 0 |

**Example (Python):**

```python
def read_nc_file(stub, filename):
    content = b""
    modified_timestamp = None
    while True:
        chunk = stub.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename=filename, offset=len(content))
        )
        version = (chunk.modified_timestamp, chunk.total_size_bytes)
        if modified_timestamp not in (None, version):
            raise RuntimeError(f"{filename} changed while it was being read")
        modified_timestamp = version
        content += chunk.content
        if chunk.end_of_file:
            return content.decode()

print(read_nc_file(stub, "my_part.ngc"))
```

The Go client has a `ReadFile(ctx, filename)` helper that does the same loop.

### ResolveFile

Resolve a name in the nc_files directory to its canonical path, and say
whether it is the program LinuxCNC has loaded. Use it before replacing or
deleting a file, to make sure you are not changing a program that is loaded
or running.

```protobuf
rpc ResolveFile(ResolveFileRequest) returns (ResolveFileResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Relative path within nc_files dir. The file does not have to exist |

**Response:** `ResolveFileResponse`

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Absolute, canonical path with symbolic links resolved. This is the file `UploadFile`, `DeleteFile` and `ReadFile` act on for this name |
| `exists` | bool | True if something exists at that path |
| `is_directory` | bool | True if it is a directory |
| `is_symlink` | bool | True if resolving the name goes through a symbolic link at any component |
| `is_loaded_program` | bool | True if this is the file LinuxCNC has loaded (`task.file`) |
| `loaded_program` | string | Canonical path of the loaded program, empty if none |
| `interp_state` | InterpState | Interpreter state when checked |

`is_loaded_program` is true when the name resolves to the same canonical path
as `task.file`, or to the same file on disk (a hard link). It compares
resolved paths, so a symbolic link to the loaded program counts, and so does
a program LinuxCNC opened through a link.

LinuxCNC keeps reporting the last program it opened, even after it has
finished. A loaded program is only *in use* while `interp_state` is not
`INTERP_IDLE`. A safe rule for clients:

- `is_loaded_program` false: the name is not the loaded program; replace or
  delete it freely.
- `is_loaded_program` true and `interp_state` is `INTERP_IDLE`: the program is
  loaded but not running. Replacing it is safe, but LinuxCNC keeps the version
  it read until the program is opened again.
- `is_loaded_program` true and any other `interp_state`: leave the file alone.

The answer is a snapshot: another client or the operator can load a program
right after you ask.

**Error codes:**

| Code | Condition |
|------|-----------|
| `INVALID_ARGUMENT` | Path traversal attempt, a symbolic link loop in the name, or an empty name |
| `UNAVAILABLE` | LinuxCNC could not be read, so the loaded program is unknown |
| `INTERNAL` | The server's NC files directory could not be resolved (a symbolic link loop), or reading LinuxCNC's status failed in a way the server does not expect. The message names the error |

If the path LinuxCNC reports for the loaded program cannot be resolved (a
symbolic link loop), `is_loaded_program` is false.

**Example (Python):**

```python
resolved = stub.ResolveFile(
    linuxcnc_pb2.ResolveFileRequest(filename="my_part.ngc")
)
if resolved.is_loaded_program and resolved.interp_state != linuxcnc_pb2.INTERP_IDLE:
    print(f"{resolved.path} is running; not touching it")
else:
    stub.DeleteFile(linuxcnc_pb2.DeleteFileRequest(filename="my_part.ngc"))
```

### GetMachineConfig

Get the machine's configuration: kinematics, which joint drives which axis,
limits, velocities and units. This is the information a client otherwise has
to guess or ask the user for, for example which joint to jog on an unhomed
gantry or lathe.

```protobuf
rpc GetMachineConfig(GetMachineConfigRequest) returns (MachineConfig)
```

**Request:** `GetMachineConfigRequest` (empty)

**Response:** `MachineConfig`

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `ini_filename` | string | status | INI file LinuxCNC was started with |
| `ini_readable` | bool | | False if the server could not read that file |
| `machine_name` | string | INI | `[EMC]MACHINE` |
| `kinematics` | string | INI | `[KINS]KINEMATICS` in full, e.g. `trivkins coordinates=XYYZ kinstype=BOTH` |
| `kinematics_module` | string | INI | Its first word, e.g. `trivkins` |
| `kinematics_type` | KinematicsType | status | Identity, forward only, inverse only or both |
| `coordinates` | string | INI | `[TRAJ]COORDINATES` without spaces, upper case |
| `axis_mask` | int32 | status | Bit 0 = X … bit 8 = W |
| `joint_count`, `spindle_count` | int32 | status | The number of joints and spindles [GetStatus](#getstatus) lists: LinuxCNC's counts, or the length of its full lists (16 and 8) if a count cannot be right (zero, negative or more than it lists) |
| `joint_axis_mapping_known` | bool | | True if the server could work out which axis each joint drives (see below) |
| `linear_units`, `angular_units` | double | status | Machine units per mm, and per degree |
| `linear_units_name` | string | status | `mm`, `inch` or `cm`; empty for anything else |
| `angular_units_name` | string | status | `degree`, `radian` or `grad`; empty for anything else |
| `max_linear_velocity`, `default_linear_velocity` | double | INI | `[TRAJ]`, machine units per second |
| `max_angular_velocity`, `default_angular_velocity` | double | INI | `[TRAJ]` |
| `lathe`, `back_tool_lathe` | bool | INI | `[DISPLAY]LATHE`, `[DISPLAY]BACK_TOOL_LATHE` |
| `max_feed_override` | double | INI | `[DISPLAY]MAX_FEED_OVERRIDE` (1.2 means 120%) |
| `min_spindle_override`, `max_spindle_override` | double | INI | `[DISPLAY]` |
| `nc_files_directory` | string | server | Directory the file RPCs and `program open` work in |
| `joints` | repeated JointConfig | | One per joint |
| `axes` | repeated AxisConfig | | One per axis in `axis_mask` |

`JointConfig`:

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `joint_number` | int32 | | |
| `joint_type` | JointType | status | Linear or angular |
| `axis_letter` | string | INI | Axis this joint drives; empty if the mapping is not known, or if the joint drives no axis (an extra joint) |
| `min_limit`, `max_limit` | double | status | Soft limits, machine units |
| `max_velocity`, `max_acceleration` | double | INI | `[JOINT_n]` |
| `home_sequence` | optional int32 | INI | `[JOINT_n]HOME_SEQUENCE`; unset if the INI has none |
| `units` | double | status | Joint units per mm or per degree |

`AxisConfig`:

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `axis_number` | int32 | | 0 = X … 8 = W |
| `letter` | string | | `X`, `Y`, … |
| `min_limit`, `max_limit` | double | status | Soft limits, machine units |
| `max_velocity`, `max_acceleration` | double | INI | `[AXIS_<letter>]` |
| `joints` | repeated int32 | INI | Joints that drive this axis; empty if the mapping is not known or no joint drives it |

Notes:

- **Joint-to-axis mapping.** With `trivkins`, joint N drives the Nth letter of
  the `coordinates=` parameter, or of `XYZABCUVW` when there is none. So a
  lathe with `trivkins coordinates=XZ` has joint 1 on Z, and a gantry with
  `coordinates=XYYZ` has joints 1 and 2 on Y. For every other kinematics
  module (scara, delta, 5-axis and so on) a joint does not correspond to one
  axis: `joint_axis_mapping_known` is false, and `axis_letter` and `joints`
  are empty.
- **Extra joints have no axis letter.** A machine can have extra joints
  (`num_extrajoints=` on the `[EMCMOT]EMCMOT` line): they are the last joints,
  LinuxCNC homes them like any joint, but they are not part of the kinematics
  and drive no axis. They are listed in `joints` with their limits, type,
  velocities and home sequence, with an empty `axis_letter`, and they appear
  in no axis's `joints`. With `trivkins`, five joints, `coordinates=XYZ` and
  two extra joints, joints 0 to 2 are X, Y and Z and joints 3 and 4 have no
  letter. So with `joint_axis_mapping_known` true, an empty `axis_letter`
  means "this joint drives no axis", not "unknown"; the number of extra
  joints is `trajectory.num_extrajoints` in the status. Do not jog such a
  joint to move an axis: jogging an axis never moves it, and after homing
  LinuxCNC drives it from the HAL pin `joint.N.posthome-cmd`.
- **INI values are read on every call** from the file named in status. They
  are the configured values: a limit changed at run time with `set_limit`
  shows in the status-based limits, but a velocity changed in HAL does not
  show anywhere here.
- **A value missing from the INI is 0** (or empty, or unset for
  `home_sequence`). If the server cannot read the INI file at all,
  `ini_readable` is false, the INI-based fields are empty and the
  status-based ones are still filled.
- **An INI value the response cannot carry is treated as missing**, with a
  warning in the server log: a number that is not finite (`nan`, `inf`, or
  one that overflows such as `1e999`) is reported as 0, and an integer
  outside the int32 range (`home_sequence`) is left unset.
- Velocities are in machine units (see `linear_units_name`) per second and
  accelerations in machine units per second squared, as written in the INI.

**Error codes:**

| Code | Condition |
|------|-----------|
| `UNAVAILABLE` | LinuxCNC could not be read |
| `INTERNAL` | The server's NC files directory could not be resolved (a symbolic link loop), or reading LinuxCNC's status failed in a way the server does not expect. The message names the error |

**Example (Python):**

```python
config = stub.GetMachineConfig(linuxcnc_pb2.GetMachineConfigRequest())
print(f"{config.machine_name}: {config.kinematics} in {config.linear_units_name}")
for joint in config.joints:
    # No letter: an extra joint if the mapping is known, otherwise unknown
    print(f"joint {joint.joint_number} -> {joint.axis_letter or '-'} "
          f"[{joint.min_limit}, {joint.max_limit}]")

# Find the joints to jog for the Y axis on an unhomed machine
y_joints = [j.joint_number for j in config.joints if j.axis_letter == "Y"]
```

### GetServerInfo

Get the server's version, whether it is connected to LinuxCNC, and LinuxCNC's
version.

```protobuf
rpc GetServerInfo(GetServerInfoRequest) returns (ServerInfo)
```

**Request:** `GetServerInfoRequest` (empty)

**Response:** `ServerInfo`

| Field | Type | Description |
|-------|------|-------------|
| `server_version` | string | Version of the linuxcnc-grpc package the server runs, for example `1.1.0`. The package version is the API version |
| `linuxcnc_connected` | bool | True once the server has connected to LinuxCNC; false while it is waiting for LinuxCNC to start |
| `linuxcnc_version` | string | LinuxCNC's version as its Python module on the server's machine reports it, for example `2.9.4`; empty if the module does not say |

**Behavior:**

- **It works while LinuxCNC is down.** A server that is
  [waiting for LinuxCNC](server.md#starting-before-linuxcnc) answers with
  `linuxcnc_connected = false`; this RPC never fails with `UNAVAILABLE`
  because of LinuxCNC.
- **It is cheap and never waits behind a command.** The server answers from
  values it already holds: it does not ask LinuxCNC anything and does not
  wait for a `SendCommand` that is in progress, so it is safe to call often
  and with a short deadline.
- **A 1.0 server answers `UNIMPLEMENTED`.** This RPC was added in 1.1. A
  client that gets `UNIMPLEMENTED` is talking to a 1.0 server and should not
  use the other RPCs and fields added since (`ReadFile`, `ResolveFile`,
  `GetMachineConfig`, `linuxcnc_serial` and so on). Any answer means 1.1 or
  later, and `server_version` says which.
- `linuxcnc_version` comes from the LinuxCNC installed on the server's
  machine, so it is filled in while `linuxcnc_connected` is false too. Only
  `linuxcnc_connected` says whether LinuxCNC is running. While connected it
  is the same string as `LinuxCNCStatus.version`, except that a version the
  server does not know is empty here and `"unknown"` there.
- `linuxcnc_connected` is what the server holds, not a check of LinuxCNC made
  for this call. It is the same state the gRPC health service reports, and
  like it does not show a crashed LinuxCNC (see
  [What happens when LinuxCNC stops](server.md#what-happens-when-linuxcnc-stops)).

**Error codes:**

| Code | Condition |
|------|-----------|
| `UNIMPLEMENTED` | The server is version 1.0, which does not have this RPC |

**Example (Python):**

```python
try:
    server_info = stub.GetServerInfo(linuxcnc_pb2.GetServerInfoRequest())
except grpc.RpcError as error:
    if error.code() != grpc.StatusCode.UNIMPLEMENTED:
        raise
    print("linuxcnc-grpc 1.0 server")
else:
    print(f"linuxcnc-grpc {server_info.server_version}, "
          f"LinuxCNC {server_info.linuxcnc_version or 'version unknown'}, "
          f"{'connected' if server_info.linuxcnc_connected else 'waiting for LinuxCNC'}")
```

---

## HalService

HAL (Hardware Abstraction Layer) introspection service. It is read-only, and
it needs LinuxCNC 2.9 or newer (see
[Requirements](server.md#requirements)).

### GetSystemStatus

Get complete HAL system status.

```protobuf
rpc GetSystemStatus(GetSystemStatusRequest) returns (HalSystemStatus)
```

**Request:** `GetSystemStatusRequest` (empty message)

**Response:** `HalSystemStatus`

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | int64 | Unix timestamp in nanoseconds |
| `pins` | HalPinInfo[] | All HAL pins |
| `signals` | HalSignalInfo[] | All HAL signals |
| `params` | HalParamInfo[] | All HAL parameters |
| `components` | HalComponentInfo[] | Groups inferred from pin and parameter names, not HAL's own component list: see [QueryComponents](#querycomponents) |
| `message_level` | MessageLevel | The RTAPI message level of the server process. `MSG_LEVEL_UNSPECIFIED` if HAL does not say |
| `is_sim` | bool | HAL's `is_sim` flag |
| `is_rt` | bool | HAL's `is_rt` flag |
| `is_userspace` | bool | HAL's `is_userspace` flag |
| `kernel_version` | string | HAL's `kernel_version`; "Not Available" on uspace builds |

The three flags and the kernel version are what LinuxCNC's `hal` Python module
reports on the server's machine.

---

### SendCommand (HAL)

```protobuf
rpc SendCommand(HalCommand) returns (HalCommandResponse)
```

The service is read-only, and `HalCommandResponse` carries only `success` and
`error`, so `SendCommand` answers three commands:

| Command | `success` | `error` |
|---------|-----------|---------|
| `component_exists` | true | Empty if the component exists, otherwise a message saying it does not |
| `component_ready` | true if the component exists | Empty if it is ready, otherwise a message |
| `pin_has_writer` | true | Empty if the pin's signal has a writer, otherwise a message |

`get_value` and the `query_*` commands return `success = false` with an error
that names the RPC to call instead ([GetValue](#getvalue),
[QueryPins](#querypins), [QuerySignals](#querysignals),
[QueryParams](#queryparams), [QueryComponents](#querycomponents)). Every other
command (creating, setting, connecting) returns `success = false`: the service
does not change HAL.

---

### GetValue

Get the value of a pin, signal, or parameter.

```protobuf
rpc GetValue(GetValueCommand) returns (GetValueResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Full name (e.g., "axis.x.pos-cmd") |

**Response:**

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether the query succeeded |
| `error` | string | Error message if failed |
| `value` | HalValue | The value |
| `type` | HalType | Data type (BIT, FLOAT, S32, etc.) |

Where HAL lists no type for a name (parameters on LinuxCNC 2.9), the type is
worked out from the value: an integer up to 2147483647 is reported as
`HAL_S32`, a larger one as `HAL_U32`. See [HalType](#haltype).

---

### QueryPins

Query pins matching a glob pattern.

```protobuf
rpc QueryPins(QueryPinsCommand) returns (QueryPinsResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `pattern` | string | Glob pattern (e.g., "axis.*", "*speed*") |

**Response:**

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether the query succeeded |
| `error` | string | Error message if failed |
| `pins` | HalPinInfo[] | Matching pins. `signal` is always empty; `has_writer` is HAL's answer for the pin: see [HalPinInfo](#halpininfo) |

**Example (Python):**

```python
request = hal_pb2.QueryPinsCommand(pattern="axis.x.*")
response = hal_stub.QueryPins(request)

for pin in response.pins:
    print(f"{pin.name}: {pin.value}")
```

---

### QuerySignals

Query signals matching a glob pattern.

```protobuf
rpc QuerySignals(QuerySignalsCommand) returns (QuerySignalsResponse)
```

Same pattern as QueryPins, returns `HalSignalInfo[]`.

---

### QueryParams

Query parameters matching a glob pattern.

```protobuf
rpc QueryParams(QueryParamsCommand) returns (QueryParamsResponse)
```

Same pattern as QueryPins, returns `HalParamInfo[]`.

---

### QueryComponents

Query components matching a glob pattern.

```protobuf
rpc QueryComponents(QueryComponentsCommand) returns (QueryComponentsResponse)
```

Same pattern as QueryPins, returns `HalComponentInfo[]`.

LinuxCNC's `hal` Python module has no component list, so the server infers
components from pin and parameter names: the part of a name before its last
dot is taken as the component. That is a naming group and not always a real
HAL component, and a component with no pins or parameters is not listed. Only
`name`, `pins` and `params` are filled in. `ready`, `type` and `pid` are never
set and `id` is 0.

---

### StreamStatus (HAL)

Subscribe to HAL status updates.

```protobuf
rpc StreamStatus(HalStreamStatusRequest) returns (stream HalSystemStatus)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `interval_ms` | int32 | Update interval in milliseconds. 0 or less means the default, 100. Values above 60000 (60 seconds) are treated as 60000 |
| `filter` | string[] | Currently ignored by the server: the stream always carries the full HAL status |

The stream ends as soon as the client cancels or disconnects. `has_writer` is
refreshed every 2 seconds, not with every update. If HAL cannot be read five
times in a row, the stream ends with `INTERNAL` and the cause.

---

### WatchValues

Watch specific values for changes.

```protobuf
rpc WatchValues(WatchRequest) returns (stream ValueChangeBatch)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `names` | string[] | Pin/signal/param names to watch. Duplicates are ignored. At most 1000 different names |
| `interval_ms` | int32 | Check interval in milliseconds. 0 or less means the default, 100. Values above 60000 (60 seconds) are treated as 60000 |

**Response:** Stream of `ValueChangeBatch` containing `ValueChange` messages:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Name that changed |
| `old_value` | HalValue | Previous value |
| `new_value` | HalValue | New value |
| `timestamp` | int64 | When change was detected |

A batch is sent only when at least one watched value changed. Two NaN readings
in a row count as unchanged. The stream ends as soon as the client cancels or
disconnects.

**Error codes:**

| Code | Condition |
|------|-----------|
| `INVALID_ARGUMENT` | No names, more than 1000 different names, or a name that cannot be read when the watch starts |
| `INTERNAL` | HAL could not be read when the watch started, or a watched name could not be read in five polls in a row (it was removed, for example). The message names an item that failed. Changes of the other names are still sent until then |

---

## Enums

### TaskMode

```protobuf
enum TaskMode {
  TASK_MODE_UNSPECIFIED = 0;
  MODE_MANUAL = 1;  // Manual/jog mode
  MODE_AUTO = 2;    // Auto/program mode
  MODE_MDI = 3;     // MDI mode
}
```

### TaskState

```protobuf
enum TaskState {
  TASK_STATE_UNSPECIFIED = 0;
  STATE_ESTOP = 1;        // E-stop active
  STATE_ESTOP_RESET = 2;  // E-stop reset, machine off
  STATE_ON = 3;           // Machine on
  STATE_OFF = 4;          // Machine off
}
```

### InterpState

```protobuf
enum InterpState {
  INTERP_STATE_UNSPECIFIED = 0;
  INTERP_IDLE = 1;     // Interpreter idle
  INTERP_READING = 2;  // Reading program
  INTERP_PAUSED = 3;   // Program paused
  INTERP_WAITING = 4;  // Waiting for input
}
```

### ExecState

```protobuf
enum ExecState {
  EXEC_STATE_UNSPECIFIED = 0;
  EXEC_ERROR = 1;
  EXEC_DONE = 2;
  EXEC_WAITING_FOR_MOTION = 3;
  EXEC_WAITING_FOR_MOTION_QUEUE = 4;
  EXEC_WAITING_FOR_IO = 5;
  EXEC_WAITING_FOR_MOTION_AND_IO = 6;
  EXEC_WAITING_FOR_DELAY = 7;
  EXEC_WAITING_FOR_SYSTEM_CMD = 8;
  EXEC_WAITING_FOR_SPINDLE_ORIENTED = 9;
}
```

### RcsStatus

```protobuf
enum RcsStatus {
  RCS_STATUS_UNSPECIFIED = 0;
  RCS_DONE = 1;   // Command completed
  RCS_EXEC = 2;   // Command executing
  RCS_ERROR = 3;  // Command error
}
```

### JogType

```protobuf
enum JogType {
  JOG_STOP = 0;       // Stop jogging
  JOG_CONTINUOUS = 1; // Continuous jog at velocity
  JOG_INCREMENT = 2;  // Incremental jog
}
```

### HalType

```protobuf
enum HalType {
  HAL_TYPE_UNSPECIFIED = 0;
  HAL_BIT = 1;    // Boolean
  HAL_FLOAT = 2;  // Double precision float
  HAL_S32 = 3;    // Signed 32-bit integer
  HAL_U32 = 4;    // Unsigned 32-bit integer
  HAL_S64 = 5;    // Signed integer that does not fit 32 bits
  HAL_U64 = 6;    // Unsigned integer that does not fit 32 bits
  HAL_PORT = 7;   // Port type (advanced)
}
```

- `HAL_S64` and `HAL_U64` are only reported for a value that does not fit 32
  bits. HAL itself has no separate 64-bit types.
- A port has no value the server can read, so a port pin is reported with an
  empty `value`. Its type is `HAL_PORT` where LinuxCNC's `hal` module reports
  the type, and `HAL_TYPE_UNSPECIFIED` where it does not (LinuxCNC 2.9).
- LinuxCNC 2.9 lists no type for parameters, so a parameter's type is worked
  out from its value. An unsigned parameter holding 2147483647 or less is
  therefore reported as `HAL_S32`.

### PinDirection

```protobuf
enum PinDirection {
  PIN_DIR_UNSPECIFIED = 0;
  HAL_IN = 1;   // Input (component reads)
  HAL_OUT = 2;  // Output (component writes)
  HAL_IO = 3;   // Bidirectional
}
```

---

## Messages

### Position

9-axis position (X, Y, Z, A, B, C, U, V, W).

```protobuf
message Position {
  double x = 1;
  double y = 2;
  double z = 3;
  double a = 4;
  double b = 5;
  double c = 6;
  double u = 7;
  double v = 8;
  double w = 9;
}
```

### PositionStatus

All position-related information.

```protobuf
message PositionStatus {
  Position position = 1;         // Commanded position
  Position actual_position = 2;  // Actual/feedback position
  Position probed_position = 3;  // Probe trip position
  Position dtg = 4;              // Distance to go
  Position g5x_offset = 5;      // Work offset (G54-G59.3)
  Position g92_offset = 6;      // G92 offset
  Position tool_offset = 7;     // Tool offset
  int32 g5x_index = 8;           // Work coordinate index
}
```

### JointStatus

Per-joint status information.

```protobuf
message JointStatus {
  int32 joint_number = 1;
  JointType joint_type = 2;   // LINEAR or ANGULAR
  double output = 11;         // Commanded position
  double input = 12;          // Feedback position
  double velocity = 13;
  bool homed = 16;
  bool enabled = 18;
  bool fault = 17;
  bool min_hard_limit = 21;
  bool max_hard_limit = 22;
  // ... additional fields
}
```

### SpindleStatus

Per-spindle status information.

```protobuf
message SpindleStatus {
  int32 spindle_number = 1;
  bool brake = 2;
  int32 direction = 3;    // -1=reverse, 0=stopped, 1=forward
  bool enabled = 4;
  double speed = 6;       // Commanded RPM
  double override = 7;    // Override scale
}
```

### ToolEntry

One tool in `tool.tool_table`.

```protobuf
message ToolEntry {
  int32 id = 1;                    // Tool number
  double x_offset = 2;
  double z_offset = 4;
  double diameter = 11;
  int32 orientation = 14;
  optional int32 table_index = 16; // Index in LinuxCNC's own tool table
  // ... additional fields
}
```

- `tool_table` holds only the entries with a tool (a tool number other than
  0), so an entry's position in the list is not its position in LinuxCNC's
  tool table.
- An entry's `id` can be -1 for an empty slot: index 0 is the spindle, and
  LinuxCNC gives it id -1 when no tool is loaded (seen on the 2.9 simulator).
  Ignore entries whose `id` is not positive.
- `table_index` is the entry's index in the tool table as LinuxCNC's status
  lists it. `tool.pocket_prepped` is an index into that same table (-1 when
  nothing is prepared), so the tool prepared for the next change is the entry
  whose `table_index` equals `pocket_prepped`. If no entry matches, the
  prepared slot is empty.
- `table_index` is unset in a status from a 1.0 server. Check for presence
  (`HasField("table_index")` in Python, `TableIndex != nil` in Go,
  `tableIndex !== undefined` in TypeScript, `table_index.is_some()` in Rust)
  rather than comparing with 0, which is a real index.

```python
status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
prepped_tool = next(
    (entry for entry in status.tool.tool_table
     if entry.HasField("table_index")
     and entry.table_index == status.tool.pocket_prepped),
    None,
)
```

### HalPinInfo

Information about a HAL pin.

```protobuf
message HalPinInfo {
  string name = 1;            // Full name (component.pin)
  string short_name = 2;      // Pin name only
  string component = 3;       // Parent component
  HalType type = 4;           // Data type
  PinDirection direction = 5; // IN/OUT/IO
  HalValue value = 6;         // Current value
  string signal = 7;          // Always empty
  bool has_writer = 8;        // The pin's signal has a writer
}
```

- `signal` is always empty: LinuxCNC's `hal` Python module does not say which
  signal a pin is connected to. `HalSignalInfo.driver` names the pin that
  drives a signal.
- `has_writer` is HAL's own answer (`hal.pin_has_writer`): true when the pin
  is connected to a signal that an output pin drives. In
  [StreamStatus](#streamstatus-hal) it is refreshed every 2 seconds.
- `direction` is `PIN_DIR_UNSPECIFIED` if HAL does not report one.

### HalValue

Universal value container.

```protobuf
message HalValue {
  oneof value {
    bool bit_value = 1;
    double float_value = 2;
    int32 s32_value = 3;
    uint32 u32_value = 4;
    int64 s64_value = 5;
    uint64 u64_value = 6;
    string port_value = 7;       // Never set by the server
  }
}
```

No field is set for a port, or for a value the server could not read.

### 64-bit integers in Node.js/TypeScript

The Node.js/TypeScript client gives every `int64` and `uint64` field as a
JavaScript `number`, which holds whole numbers exactly only up to 2^53 - 1
(`Number.MAX_SAFE_INTEGER`).

- **Timestamps** (`timestamp` and `modified_timestamp` fields) are sent in
  nanoseconds, which are beyond that range. The client scales them to
  milliseconds, so `new Date(status.timestamp)` works as it is.
- **Everything else** (`s64_value` and `u64_value` in `HalValue`,
  `ErrorMessage.sequence`, file sizes and offsets) is not scaled. A value
  beyond 2^53 arrives as the nearest number, so its low digits are lost:
  9007199254740993 arrives as 9007199254740992. Up to 1.0 such a value was
  divided by 1,000,000.

The Python, Go and Rust clients use 64-bit integer types and get every value
exactly, with timestamps in nanoseconds.

---

## Proto Files

The complete proto definitions are in the `proto/` directory:

- [`proto/linuxcnc.proto`](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/proto/linuxcnc.proto) - LinuxCNCService
- [`proto/hal.proto`](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/proto/hal.proto) - HalService
