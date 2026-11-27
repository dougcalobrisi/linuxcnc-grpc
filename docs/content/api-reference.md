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
- [HalService](#halservice)
  - [GetSystemStatus](#getsystemstatus)
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
| `joints` | JointStatus[] | Per-joint status (up to 16) |
| `axes` | AxisStatus[] | Per-axis status (up to 9: XYZABCUVW) |
| `spindles` | SpindleStatus[] | Per-spindle status (up to 8) |
| `tool` | ToolStatus | Tool table and current tool |
| `io` | IOStatus | Digital/analog I/O state |
| `gcode` | GCodeStatus | Active G-codes and M-codes |
| `limits` | LimitStatus | Limit switch states |
| `errors` | ErrorMessage[] | Always empty. Error messages are delivered only by [StreamErrors](#streamerrors) |

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
| `timeout` | double | Timeout in seconds. 0, a negative number or NaN means the default, 5 seconds. An infinite timeout waits until the command finishes or the client goes away |
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
of the earlier command is not known from the wait. A client that needs to
know a motion has finished should also read the status (`task.interp_state`,
`trajectory.inpos`, `trajectory.current_vel`). See
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
| `interval_ms` | int32 | Update interval in milliseconds (default: 100) |

**Response:** Stream of `LinuxCNCStatus` messages

**Example (Python):**

```python
request = linuxcnc_pb2.StreamStatusRequest(interval_ms=100)

for status in stub.StreamStatus(request):
    pos = status.position.actual_position
    print(f"X={pos.x:.3f} Y={pos.y:.3f} Z={pos.z:.3f}")
```

---

### StreamErrors

Subscribe to error messages.

```protobuf
rpc StreamErrors(StreamErrorsRequest) returns (stream ErrorMessage)
```

**Request:** `StreamErrorsRequest` (empty message)

**Response:** Stream of `ErrorMessage`

| Field | Type | Description |
|-------|------|-------------|
| `type` | ErrorType | OPERATOR_ERROR, NML_ERROR, etc. |
| `message` | string | Error message text |
| `timestamp` | int64 | Unix timestamp in nanoseconds |

> **`StreamErrors` consumes messages.** LinuxCNC's error channel is a single
> queue, so each message this stream delivers is removed and will not appear
> in the machine's GUI (AXIS, gmoccapy and so on). This is the only way to
> receive error messages: `LinuxCNCStatus.errors` is always empty.

---

### File Management

The following RPCs manage G-code files in the NC files directory (default: `/home/linuxcnc/linuxcnc/nc_files`, configurable via `--nc-files` flag or `LINUXCNC_NC_FILES` environment variable). See [Server Configuration](server.md#nc-files-directory) for details.

All filenames are relative to this directory. Path traversal outside it is rejected. An upload can hold at most 10 MiB (10,485,760) characters of text, and the whole request must fit in the server's 11 MiB receive limit (see [UploadFile](#uploadfile)).

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
| `fail_if_exists` | bool | If true, fail when file already exists (default: false = overwrite) |

**Response:** `UploadFileResponse`

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Absolute path where file was written |
| `overwritten` | bool | True if an existing file was replaced |

**Error codes:**

| Code | Condition |
|------|-----------|
| `INVALID_ARGUMENT` | Empty filename, empty content, null bytes, path traversal, or content longer than 10,485,760 characters (10 MiB) |
| `ALREADY_EXISTS` | File exists and `fail_if_exists` is true |
| `RESOURCE_EXHAUSTED` | The request message is larger than the server's receive limit of 11 MiB (11,534,336 bytes). gRPC rejects it before `UploadFile` runs |

The 10 MiB limit counts characters, not bytes (`content` is a string). For plain ASCII G-code the two are the same. Text with non-ASCII characters takes more than one byte per character, so it can stay under the character limit and still exceed the receive limit.

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

**Error codes:**

| Code | Condition |
|------|-----------|
| `NOT_FOUND` | Subdirectory does not exist |
| `INVALID_ARGUMENT` | Path traversal attempt |

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
| `INVALID_ARGUMENT` | Path traversal attempt or target is a directory |

**Example (Python):**

```python
response = stub.DeleteFile(
    linuxcnc_pb2.DeleteFileRequest(filename="my_part.ngc")
)
print(f"Deleted: {response.path}")
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
| `components` | HalComponentInfo[] | All HAL components |
| `is_sim` | bool | Running in simulation mode |
| `is_rt` | bool | Running real-time kernel |

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
| `pins` | HalPinInfo[] | Matching pins |

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

---

### StreamStatus (HAL)

Subscribe to HAL status updates.

```protobuf
rpc StreamStatus(HalStreamStatusRequest) returns (stream HalSystemStatus)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `interval_ms` | int32 | Update interval in milliseconds |
| `filter` | string[] | Currently ignored by the server: the stream always carries the full HAL status |

---

### WatchValues

Watch specific values for changes.

```protobuf
rpc WatchValues(WatchRequest) returns (stream ValueChangeBatch)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `names` | string[] | Pin/signal/param names to watch |
| `interval_ms` | int32 | Check interval in milliseconds |

**Response:** Stream of `ValueChangeBatch` containing `ValueChange` messages:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Name that changed |
| `old_value` | HalValue | Previous value |
| `new_value` | HalValue | New value |
| `timestamp` | int64 | When change was detected |

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
  HAL_S64 = 5;    // Signed 64-bit integer
  HAL_U64 = 6;    // Unsigned 64-bit integer
  HAL_PORT = 7;   // Port type (advanced)
}
```

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
  string signal = 7;          // Connected signal
  bool has_writer = 8;        // Has a writer connected
}
```

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
    string port_value = 7;       // Port type (advanced)
  }
}
```

---

## Proto Files

The complete proto definitions are in the `proto/` directory:

- [`proto/linuxcnc.proto`](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/proto/linuxcnc.proto) - LinuxCNCService
- [`proto/hal.proto`](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/proto/hal.proto) - HalService
