---
title: "Client Quickstart"
weight: 10
---

Install a client library and make your first API calls. This guide assumes you already have a [running gRPC server](server-setup.md).

## Client Installation

{{%  tabs "client-install"  %}}
{{%  tab "Python"  %}}
```bash
pip install linuxcnc-grpc
```
{{%  /tab  %}}
{{%  tab "Go"  %}}
```bash
go get github.com/dougcalobrisi/linuxcnc-grpc
```
{{%  /tab  %}}
{{%  tab "Node.js / TypeScript"  %}}
```bash
npm install linuxcnc-grpc
```
{{%  /tab  %}}
{{%  tab "Rust"  %}}
Add to `Cargo.toml`:

```toml
[dependencies]
linuxcnc-grpc = "1.0"
tokio = { version = "1", features = ["full"] }
tonic = "0.12"
```
{{%  /tab  %}}
{{%  /tabs  %}}

## Quick Start Examples

{{%  tabs "quickstart"  %}}
{{%  tab "Python"  %}}
```python
import grpc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

# Connect to server
channel = grpc.insecure_channel("localhost:50051")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

# Get status
status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

# Print position
pos = status.position.actual_position
print(f"Position: X={pos.x:.3f} Y={pos.y:.3f} Z={pos.z:.3f}")

# Print machine state
print(f"Mode: {linuxcnc_pb2.TaskMode.Name(status.task.task_mode)}")
print(f"State: {linuxcnc_pb2.TaskState.Name(status.task.task_state)}")

channel.close()
```
{{%  /tab  %}}
{{%  tab "Go"  %}}
```go
package main

import (
    "context"
    "fmt"
    "log"

    pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"
)

func main() {
    // Connect to server
    conn, err := grpc.NewClient("localhost:50051",
        grpc.WithTransportCredentials(insecure.NewCredentials()))
    if err != nil {
        log.Fatalf("Failed to connect: %v", err)
    }
    defer conn.Close()

    client := pb.NewLinuxCNCServiceClient(conn)

    // Get status
    status, err := client.GetStatus(context.Background(), &pb.GetStatusRequest{})
    if err != nil {
        log.Fatalf("GetStatus failed: %v", err)
    }

    // Print position
    pos := status.Position.ActualPosition
    fmt.Printf("Position: X=%.3f Y=%.3f Z=%.3f\n", pos.X, pos.Y, pos.Z)
}
```
{{%  /tab  %}}
{{%  tab "Node.js / TypeScript"  %}}
```typescript
import * as grpc from "@grpc/grpc-js";
import { LinuxCNCServiceClient, GetStatusRequest, TaskMode, TaskState } from "linuxcnc-grpc";

// Connect to server
const client = new LinuxCNCServiceClient(
  "localhost:50051",
  grpc.credentials.createInsecure()
);

// Get status
client.getStatus(GetStatusRequest.create(), (err, status) => {
  if (err) {
    console.error("Error:", err);
    return;
  }

  // Print position
  const pos = status!.position!.actualPosition!;
  console.log(`Position: X=${pos.x.toFixed(3)} Y=${pos.y.toFixed(3)} Z=${pos.z.toFixed(3)}`);

  // Print machine state
  console.log(`Mode: ${TaskMode[status!.task!.taskMode]}`);
  console.log(`State: ${TaskState[status!.task!.taskState]}`);
});
```
{{%  /tab  %}}
{{%  tab "Rust"  %}}
```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::GetStatusRequest;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Connect to server
    let mut client = LinuxCncServiceClient::connect("http://localhost:50051").await?;

    // Get status
    let response = client.get_status(GetStatusRequest {}).await?;
    let status = response.into_inner();

    // Print position
    if let Some(position) = &status.position {
        if let Some(pos) = &position.actual_position {
            println!("Position: X={:.3} Y={:.3} Z={:.3}", pos.x, pos.y, pos.z);
        }
    }

    Ok(())
}
```
{{%  /tab  %}}
{{%  /tabs  %}}

## Sending Commands

Commands follow a consistent pattern across all languages:

1. Create a `LinuxCNCCommand` message
2. Set the specific command field (state, mode, mdi, jog, etc.)
3. Call `SendCommand`
4. Optionally call `WaitComplete` to wait for execution

Two things to know before you rely on the responses:

- `RCS_DONE` from `SendCommand` means the command was *dispatched*, not that
  it finished. The response's `linuxcnc_serial` identifies the command to
  LinuxCNC; pass it to `WaitComplete` to wait on that command's serial. The
  answer is exact as long as nothing else is sent to LinuxCNC meanwhile: once
  a later command has been echoed, from any client, it is `RCS_DONE` and does
  not say how the earlier command ended (see
  [Command and Error Semantics](server.md#waitcomplete)).
- The server does not switch modes for you.

### Example: MDI Command (Python)

```python
import time
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

# Ensure machine is on and in MDI mode first
def send_mdi(stub, gcode):
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = 1
    cmd.timestamp = int(time.time() * 1e9)
    cmd.mdi.command = gcode

    response = stub.SendCommand(cmd)

    if response.status != linuxcnc_pb2.RCS_DONE:
        print(f"Error: {response.error_message}")
        return False

    # Wait on this command's serial
    wait_req = linuxcnc_pb2.WaitCompleteRequest(timeout=30.0)
    if response.HasField("linuxcnc_serial"):
        wait_req.linuxcnc_serial = response.linuxcnc_serial
    else:
        print("Note: no linuxcnc_serial; waiting on the latest command sent by any client")
    response = stub.WaitComplete(wait_req)

    # RCS_EXEC here means the wait timed out and the command is still running
    return response.status == linuxcnc_pb2.RCS_DONE

# Usage
send_mdi(stub, "G0 X10 Y10")
```

### Example: Jogging (Python)

```python
def jog_axis(stub, axis, velocity):
    """Start continuous jog on an axis."""
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = 1
    cmd.timestamp = int(time.time() * 1e9)
    cmd.jog.type = linuxcnc_pb2.JOG_CONTINUOUS
    cmd.jog.is_joint = False  # Axis mode
    cmd.jog.index = axis  # 0=X, 1=Y, 2=Z
    cmd.jog.velocity = velocity

    return stub.SendCommand(cmd)

def jog_stop(stub, axis):
    """Stop jogging an axis."""
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = 2
    cmd.timestamp = int(time.time() * 1e9)
    cmd.jog.type = linuxcnc_pb2.JOG_STOP
    cmd.jog.is_joint = False
    cmd.jog.index = axis

    return stub.SendCommand(cmd)
```

## Streaming Status

Instead of polling, subscribe to real-time updates:

{{%  tabs "streaming"  %}}
{{%  tab "Python"  %}}
```python
def stream_status(stub):
    request = linuxcnc_pb2.StreamStatusRequest(interval_ms=100)

    for status in stub.StreamStatus(request):
        pos = status.position.actual_position
        print(f"X={pos.x:.3f} Y={pos.y:.3f} Z={pos.z:.3f}")
```
{{%  /tab  %}}
{{%  tab "Go"  %}}
```go
func streamStatus(client pb.LinuxCNCServiceClient) {
    stream, err := client.StreamStatus(context.Background(),
        &pb.StreamStatusRequest{IntervalMs: 100})
    if err != nil {
        log.Fatal(err)
    }

    for {
        status, err := stream.Recv()
        if err != nil {
            break
        }
        pos := status.Position.ActualPosition
        fmt.Printf("X=%.3f Y=%.3f Z=%.3f\n", pos.X, pos.Y, pos.Z)
    }
}
```
{{%  /tab  %}}
{{%  /tabs  %}}

## HAL Queries

Query HAL pins, signals, and parameters:

### Python

```python
from linuxcnc_pb import hal_pb2, hal_pb2_grpc

hal_stub = hal_pb2_grpc.HalServiceStub(channel)

# Query all pins matching a pattern
request = hal_pb2.QueryPinsCommand(pattern="axis.*position*")
response = hal_stub.QueryPins(request)

for pin in response.pins:
    print(f"{pin.name}: {pin.value}")
```

## Machine Configuration

`GetMachineConfig` tells a client what kind of machine it is talking to:
units, limits, and which joint drives which axis.

```python
config = stub.GetMachineConfig(linuxcnc_pb2.GetMachineConfigRequest())
print(config.machine_name, config.linear_units_name)
for joint in config.joints:
    print(joint.joint_number, joint.axis_letter, joint.min_limit, joint.max_limit)
```

See [GetMachineConfig](api-reference.md#getmachineconfig).

## Files

`UploadFile`, `ListFiles`, `ReadFile` and `DeleteFile` manage G-code files in
the server's NC files directory, and `ResolveFile` tells you whether a name is
the program LinuxCNC has loaded. See
[File Management](api-reference.md#file-management).

## Error Handling

All gRPC calls can raise `grpc.RpcError`. Common status codes:

| Code | Meaning |
|------|---------|
| `UNAVAILABLE` | Server not reachable or LinuxCNC disconnected |
| `INVALID_ARGUMENT` | A file RPC was given a bad argument: a file name outside the NC files directory, an empty name, empty or too large upload content |
| `NOT_FOUND` | A file that does not exist |
| `FAILED_PRECONDITION` | Machine not in correct state for command |
| `INTERNAL` | Server-side error |

A rejected command is not a gRPC error. If the server rejects it before
sending (a joint or spindle index out of range, an empty MDI command, an
unknown command), `SendCommand` returns a normal `CommandResponse` with
`RCS_ERROR` and the reason in `error_message`. `RCS_ERROR` can also follow a
command that was partly sent (see
[`RCS_ERROR` from `SendCommand`](server.md#rcs_error-from-sendcommand)).
If LinuxCNC rejects it afterwards, `WaitComplete` returns `RCS_ERROR` or
LinuxCNC reports the problem as an error message (see [StreamErrors](api-reference.md#streamerrors)). So
check `response.status` as well as catching `grpc.RpcError`.

```python
try:
    status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
except grpc.RpcError as e:
    print(f"Error: {e.code()}: {e.details()}")
```

## Next Steps

- [Server Configuration](server.md) - Auto-start, TLS, and advanced setup
- [API Reference](api-reference.md) - Complete API documentation
- [Examples Guide](examples.md) - Detailed example walkthroughs
