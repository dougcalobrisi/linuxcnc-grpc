# API Reference

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
| `errors` | ErrorMessage[] | Pending error messages |

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

**Response:** `CommandResponse`

| Field | Type | Description |
|-------|------|-------------|
| `serial` | int32 | Echo of command serial |
| `status` | RcsStatus | RCS_DONE, RCS_EXEC, or RCS_ERROR |
| `error_message` | string | Error details if status is RCS_ERROR |

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

---

### WaitComplete

Wait for a command to complete execution.

```protobuf
rpc WaitComplete(WaitCompleteRequest) returns (CommandResponse)
```

**Request:**

| Field | Type | Description |
|-------|------|-------------|
| `serial` | int32 | Command serial to wait for |
| `timeout` | double | Timeout in seconds |

**Response:** `CommandResponse` (same as SendCommand)

**Example (Python):**

```python
# Send MDI command
cmd = linuxcnc_pb2.LinuxCNCCommand()
cmd.serial = 1
cmd.mdi.command = "G0 X10 Y10"
stub.SendCommand(cmd)

# Wait for completion
request = linuxcnc_pb2.WaitCompleteRequest(serial=1, timeout=30.0)
response = stub.WaitComplete(request)

if response.status == linuxcnc_pb2.RCS_DONE:
    print("Command completed")
```

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

---

### File Management

The following RPCs manage G-code files in the NC files directory (default: `/home/linuxcnc/linuxcnc/nc_files`, configurable via `--nc-files` flag or `LINUXCNC_NC_FILES` environment variable). See [Server Configuration](server.md#nc-files-directory) for details.

All filenames are relative to this directory. Path traversal outside it is rejected. Maximum upload size is 10 MB.

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
| `INVALID_ARGUMENT` | Empty filename, empty content, null bytes, path traversal, or content too large (>10MB) |
| `ALREADY_EXISTS` | File exists and `fail_if_exists` is true |

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

HAL (Hardware Abstraction Layer) introspection service.

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
| `filter` | string[] | Optional component filter |

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
  Position g5x_offset = 5;       // Work offset (G54-G59.3)
  Position g92_offset = 6;       // G92 offset
  Position tool_offset = 7;      // Tool offset
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

- [`proto/linuxcnc.proto`](../proto/linuxcnc.proto) - LinuxCNCService
- [`proto/hal.proto`](../proto/hal.proto) - HalService
