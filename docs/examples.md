# Examples Guide

Walkthrough of the example code provided with linuxcnc-grpc.

## Overview

Examples are provided in four languages, each implementing the same functionality:

| Example | Description |
|---------|-------------|
| `get_status` | Poll and display machine status |
| `stream_status` | Real-time status streaming |
| `jog_axis` | Interactive jogging with keyboard |
| `mdi_command` | Execute G-code via MDI |
| `hal_query` | Query HAL pins, signals, parameters |

## Directory Structure

```
examples/
├── python/
│   ├── get_status.py
│   ├── stream_status.py
│   ├── jog_axis.py
│   ├── mdi_command.py
│   └── hal_query.py
├── go/
│   └── cmd/
│       ├── get_status/
│       ├── stream_status/
│       ├── jog_axis/
│       ├── mdi_command/
│       └── hal_query/
├── node/
│   ├── get_status.ts
│   ├── stream_status.ts
│   ├── jog_axis.ts
│   ├── mdi_command.ts
│   └── hal_query.ts
├── rust/
│   ├── Cargo.toml
│   └── src/bin/
│       ├── get_status.rs
│       ├── stream_status.rs
│       ├── jog_axis.rs
│       ├── mdi_command.rs
│       └── hal_query.rs
└── README.md
```

## Running Examples

### Prerequisites

1. LinuxCNC running (or use a mock server for testing)
2. gRPC server running on the LinuxCNC machine
3. Language-specific dependencies installed

### Python

```bash
cd examples/python

# Install dependencies (if using virtualenv)
pip install grpcio linuxcnc-grpc

# Run an example
python get_status.py --host localhost --port 50051
```

### Go

```bash
cd examples/go

# Download dependencies
go mod download

# Run an example
go run ./cmd/get_status --host localhost --port 50051
```

### Node.js / TypeScript

```bash
cd examples/node

# Install dependencies
npm install

# Run an example
npx tsx get_status.ts --host localhost --port 50051
```

### Rust

```bash
cd examples/rust

# Build all examples
cargo build --release

# Run an example
cargo run --bin get_status -- --host localhost --port 50051

# Or run the built binary directly
./target/release/get_status --host localhost --port 50051
```

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

# Request status
status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

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

### Key code (Python)

```python
# Start streaming with 100ms interval
request = linuxcnc_pb2.StreamStatusRequest(interval_ms=100)

for status in stub.StreamStatus(request):
    pos = status.position.actual_position
    print(f"\rX={pos.x:8.3f} Y={pos.y:8.3f} Z={pos.z:8.3f}", end="")
```

### Key code (Go)

```go
stream, err := client.StreamStatus(ctx, &pb.StreamStatusRequest{
    IntervalMs: 100,
})

for {
    status, err := stream.Recv()
    if err == io.EOF {
        break
    }
    pos := status.Position.ActualPosition
    fmt.Printf("X=%.3f Y=%.3f Z=%.3f\n", pos.X, pos.Y, pos.Z)
}
```

### Output

```
Streaming status updates (Ctrl+C to stop)...
[  1] X=  0.000 Y=  0.000 Z=  0.000 | Feed:100% Vel:  0.00
[  2] X=  0.000 Y=  0.000 Z=  0.000 | Feed:100% Vel:  0.00
[  3] X=  0.500 Y=  0.000 Z=  0.000 | Feed:100% Vel: 10.00
...
```

---

## jog_axis

Interactive jogging with keyboard controls.

### What it demonstrates

- Sending state commands (ESTOP_RESET, ON)
- Sending mode commands (MANUAL)
- Jogging with `JOG_CONTINUOUS` and `JOG_STOP`
- Incremental jogging with `JOG_INCREMENT`
- Keyboard input handling

### Key code (Python)

```python
def jog_start(stub, axis, velocity):
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = next_serial()
    cmd.jog.type = linuxcnc_pb2.JOG_CONTINUOUS
    cmd.jog.is_joint = False  # Axis mode (vs joint mode)
    cmd.jog.index = axis      # 0=X, 1=Y, 2=Z
    cmd.jog.velocity = velocity
    return stub.SendCommand(cmd)

def jog_stop(stub, axis):
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = next_serial()
    cmd.jog.type = linuxcnc_pb2.JOG_STOP
    cmd.jog.index = axis
    return stub.SendCommand(cmd)
```

### Controls

```
Keyboard Controls:
  Arrow keys: Jog X/Y axes
  Page Up/Down: Jog Z axis
  +/-: Adjust jog speed
  Space: Emergency stop
  Q: Quit
```

---

## mdi_command

Execute G-code commands via MDI (Manual Data Input).

### What it demonstrates

- Checking and setting machine state
- Checking and setting task mode
- Sending MDI commands
- Using `WaitComplete` to wait for execution
- Interactive command loop

### Key code (Python)

```python
def ensure_mdi_ready(client):
    """Ensure machine is ready for MDI commands."""
    status = client.get_status()

    # Reset E-stop if needed
    if status.task.task_state == linuxcnc_pb2.STATE_ESTOP:
        client.set_state(linuxcnc_pb2.STATE_ESTOP_RESET)

    # Power on
    if status.task.task_state != linuxcnc_pb2.STATE_ON:
        client.set_state(linuxcnc_pb2.STATE_ON)

    # Set MDI mode
    if status.task.task_mode != linuxcnc_pb2.MODE_MDI:
        client.set_mode(linuxcnc_pb2.MODE_MDI)

def execute_mdi(client, gcode):
    """Execute G-code and wait for completion."""
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = next_serial()
    cmd.mdi.command = gcode
    response = client.send_command(cmd)

    # Wait for completion
    response = client.wait_complete(cmd.serial, timeout=60.0)
    return response.status == linuxcnc_pb2.RCS_DONE
```

### Usage

```bash
# Single command
python mdi_command.py "G0 X10 Y10"

# Interactive mode
python mdi_command.py --interactive
```

### Interactive session

```
MDI> G0 X10 Y10
Executing: G0 X10 Y10
  Waiting for completion...
  Done.
Position: X=10.0000 Y=10.0000 Z=0.0000

MDI> G1 X20 F100
Executing: G1 X20 F100
  Waiting for completion...
  Done.
Position: X=20.0000 Y=10.0000 Z=0.0000

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
- Formatting HAL values

### Key code (Python)

```python
# Create HAL service stub
hal_stub = hal_pb2_grpc.HalServiceStub(channel)

# Query pins matching pattern
request = hal_pb2.QueryPinsCommand(pattern="axis.*")
response = hal_stub.QueryPins(request)

for pin in response.pins:
    value = format_hal_value(pin.value)
    direction = hal_pb2.PinDirection.Name(pin.direction)
    print(f"{pin.name}: {value} ({direction})")
```

### Usage

```bash
# Query all axis pins
python hal_query.py --pins "axis.*"

# Query spindle signals
python hal_query.py --signals "spindle*"

# Query all motion parameters
python hal_query.py --params "motion.*"

# List all components
python hal_query.py --components "*"
```

### Output

```
=== HAL Pins matching "axis.x.*" ===
axis.x.pos-cmd: 10.500000 (HAL_OUT)
axis.x.pos-fb: 10.499823 (HAL_IN)
axis.x.vel-cmd: 0.000000 (HAL_OUT)
axis.x.homed: True (HAL_OUT)

=== HAL Signals matching "spindle*" ===
spindle-speed-out: 1200.000000
spindle-at-speed: True
spindle-on: True
```

---

## Testing with Mock Server

For development without a real LinuxCNC installation, use the mock server:

```bash
# Start mock server
python tests/mock_server.py --port 50051

# Run examples against it
python examples/python/get_status.py --port 50051
```

The mock server simulates:
- All status fields with realistic values
- Command responses
- Status streaming
- HAL system status

---

## Common Patterns

### Client Wrapper Class

Many examples use a client wrapper for cleaner code:

```python
class LinuxCNCClient:
    def __init__(self, host, port):
        self.channel = grpc.insecure_channel(f"{host}:{port}")
        self.stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(self.channel)
        self._serial = 0

    def _next_serial(self):
        self._serial += 1
        return self._serial

    def get_status(self):
        return self.stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

    def send_command(self, cmd):
        cmd.serial = self._next_serial()
        cmd.timestamp = int(time.time() * 1e9)
        return self.stub.SendCommand(cmd)
```

### Error Handling

```python
try:
    status = stub.GetStatus(request)
except grpc.RpcError as e:
    if e.code() == grpc.StatusCode.UNAVAILABLE:
        print("Server not available")
    elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        print("Request timed out")
    else:
        print(f"Error: {e.code()}: {e.details()}")
```

### Graceful Shutdown

```python
import signal

def signal_handler(sig, frame):
    print("\nShutting down...")
    channel.close()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
```
