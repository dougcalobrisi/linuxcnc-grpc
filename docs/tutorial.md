# Step-by-Step Tutorial

A hands-on guide to connecting to a remote LinuxCNC machine from your development box using any of the supported client languages.

## Prerequisites

- **LinuxCNC** running on a machine (physical or simulation)
- **Network access** between your dev machine and the LinuxCNC machine
- The LinuxCNC machine's IP address (referred to as `LINUXCNC_IP` below)

## Step 1: Install and Start the gRPC Server

SSH into your LinuxCNC machine and install the server:

```bash
pip install linuxcnc-grpc
```

Start the server (LinuxCNC must already be running):

```bash
linuxcnc-grpc --host 0.0.0.0 --port 50051
```

> `--host 0.0.0.0` makes the server listen on all network interfaces so remote clients can connect. See [Server Configuration](server.md) for auto-start setup, TLS, and advanced options.

Verify the server is reachable from your dev machine:

```bash
# From your dev machine
grpcurl -plaintext LINUXCNC_IP:50051 list
```

## Step 2: Set Up Your Client Project

Choose your language and follow the setup instructions below.

---

### Python

#### 2a. Install dependencies

```bash
mkdir my-cnc-app && cd my-cnc-app
python -m venv venv
source venv/bin/activate
pip install linuxcnc-grpc
```

#### 3a. Get machine status

Create `status.py`:

```python
import grpc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

# Connect to your LinuxCNC machine
channel = grpc.insecure_channel("LINUXCNC_IP:50051")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

try:
    status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

    # Print position
    pos = status.position.actual_position
    print(f"Position: X={pos.x:.4f} Y={pos.y:.4f} Z={pos.z:.4f}")

    # Print machine state
    print(f"Mode:  {linuxcnc_pb2.TaskMode.Name(status.task.task_mode)}")
    print(f"State: {linuxcnc_pb2.TaskState.Name(status.task.task_state)}")
except grpc.RpcError as e:
    print(f"Error: {e.code()}: {e.details()}")
finally:
    channel.close()
```

Run it:

```bash
python status.py
```

#### 4a. Stream real-time position updates

Create `stream.py`:

```python
import grpc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

channel = grpc.insecure_channel("LINUXCNC_IP:50051")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

request = linuxcnc_pb2.StreamStatusRequest()
request.interval_ms = 100  # 10 updates per second

try:
    for status in stub.StreamStatus(request):
        pos = status.position.actual_position
        print(f"\rX={pos.x:8.3f} Y={pos.y:8.3f} Z={pos.z:8.3f}", end="", flush=True)
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    channel.close()
```

#### 5a. Send commands (MDI G-code)

Create `mdi.py`:

```python
import time
import grpc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

channel = grpc.insecure_channel("LINUXCNC_IP:50051")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

# Ensure machine is on and in MDI mode
def set_state(state):
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = 1
    cmd.timestamp = int(time.time() * 1e9)
    cmd.state.state = state
    return stub.SendCommand(cmd)

def set_mode(mode):
    cmd = linuxcnc_pb2.LinuxCNCCommand()
    cmd.serial = 2
    cmd.timestamp = int(time.time() * 1e9)
    cmd.mode.mode = mode
    return stub.SendCommand(cmd)

# Power on and switch to MDI mode
set_state(linuxcnc_pb2.STATE_ESTOP_RESET)
time.sleep(0.1)
set_state(linuxcnc_pb2.STATE_ON)
time.sleep(0.1)
set_mode(linuxcnc_pb2.MODE_MDI)
time.sleep(0.1)

# Send a G-code command
cmd = linuxcnc_pb2.LinuxCNCCommand()
cmd.serial = 3
cmd.timestamp = int(time.time() * 1e9)
cmd.mdi.command = "G0 X10 Y10"
response = stub.SendCommand(cmd)

if response.status == linuxcnc_pb2.RCS_ERROR:
    print(f"Error: {response.error_message}")
else:
    # Wait for completion
    wait_req = linuxcnc_pb2.WaitCompleteRequest(serial=3, timeout=30.0)
    result = stub.WaitComplete(wait_req)
    print(f"Command completed: {linuxcnc_pb2.RcsStatus.Name(result.status)}")

channel.close()
```

---

### Node.js / TypeScript

#### 2b. Install dependencies

```bash
mkdir my-cnc-app && cd my-cnc-app
npm init -y
npm install linuxcnc-grpc @grpc/grpc-js
npm install -D typescript tsx @types/node
npx tsc --init
```

#### 3b. Get machine status

Create `status.ts`:

```typescript
import { Metadata } from "@grpc/grpc-js";
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  taskModeToJSON,
  taskStateToJSON,
  credentials,
} from "linuxcnc-grpc";

// Connect to your LinuxCNC machine
const client = new LinuxCNCServiceClient(
  "LINUXCNC_IP:50051",
  credentials.createInsecure()
);

const deadline = new Date(Date.now() + 5000);
client.getStatus(GetStatusRequest.create(), new Metadata(), { deadline }, (err, status) => {
  if (err) {
    console.error(`Error: ${err.code}: ${err.details}`);
    client.close();
    process.exit(1);
  }

  // Print position
  const pos = status!.position!.actualPosition!;
  console.log(`Position: X=${pos.x.toFixed(4)} Y=${pos.y.toFixed(4)} Z=${pos.z.toFixed(4)}`);

  // Print machine state
  console.log(`Mode:  ${taskModeToJSON(status!.task!.taskMode)}`);
  console.log(`State: ${taskStateToJSON(status!.task!.taskState)}`);

  client.close();
});
```

Run it:

```bash
npx tsx status.ts
```

#### 4b. Stream real-time position updates

Create `stream.ts`:

```typescript
import {
  LinuxCNCServiceClient,
  StreamStatusRequest,
  LinuxCNCStatus,
  credentials,
} from "linuxcnc-grpc";

const client = new LinuxCNCServiceClient(
  "LINUXCNC_IP:50051",
  credentials.createInsecure()
);

const request = StreamStatusRequest.create({ intervalMs: 100 });
const stream = client.streamStatus(request);

stream.on("data", (status: LinuxCNCStatus) => {
  const pos = status.position!.actualPosition!;
  process.stdout.write(
    `\rX=${pos.x.toFixed(3).padStart(8)} Y=${pos.y.toFixed(3).padStart(8)} Z=${pos.z.toFixed(3).padStart(8)}  `
  );
});

stream.on("error", (err) => {
  console.error(`\nStream error: ${err}`);
  client.close();
  process.exit(1);
});

// Handle Ctrl+C
process.on("SIGINT", () => {
  stream.cancel();
  console.log("\nStopped.");
  client.close();
});
```

#### 5b. Send commands (MDI G-code)

Create `mdi.ts`:

```typescript
import { Metadata } from "@grpc/grpc-js";
import {
  LinuxCNCServiceClient,
  LinuxCNCCommand,
  WaitCompleteRequest,
  TaskMode,
  TaskState,
  RcsStatus,
  credentials,
} from "linuxcnc-grpc";

const client = new LinuxCNCServiceClient(
  "LINUXCNC_IP:50051",
  credentials.createInsecure()
);

const RPC_DEADLINE = 5000;

function sendCommand(cmd: LinuxCNCCommand): Promise<any> {
  cmd.timestamp = Date.now() * 1000000;
  return new Promise((resolve, reject) => {
    client.sendCommand(cmd, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, resp) => {
      if (err) reject(err);
      else resolve(resp);
    });
  });
}

function waitComplete(serial: number, timeout: number): Promise<any> {
  const request = WaitCompleteRequest.create({ serial, timeout });
  const deadline = new Date(Date.now() + (timeout * 1000) + 5000);
  return new Promise((resolve, reject) => {
    client.waitComplete(request, new Metadata(), { deadline }, (err, resp) => {
      if (err) reject(err);
      else resolve(resp);
    });
  });
}

async function main() {
  // Power on and switch to MDI mode
  let cmd = LinuxCNCCommand.create({ serial: 1, state: { state: TaskState.STATE_ESTOP_RESET } });
  await sendCommand(cmd);

  cmd = LinuxCNCCommand.create({ serial: 2, state: { state: TaskState.STATE_ON } });
  await sendCommand(cmd);

  cmd = LinuxCNCCommand.create({ serial: 3, mode: { mode: TaskMode.MODE_MDI } });
  await sendCommand(cmd);

  // Send G-code
  cmd = LinuxCNCCommand.create({ serial: 4, mdi: { command: "G0 X10 Y10" } });
  const response = await sendCommand(cmd);

  if (response.status === RcsStatus.RCS_ERROR) {
    console.error(`Error: ${response.errorMessage}`);
  } else {
    console.log("Waiting for completion...");
    const result = await waitComplete(4, 30.0);
    console.log(`Command completed: ${RcsStatus[result.status]}`);
  }

  client.close();
}

main().catch((err) => {
  console.error(err);
  client.close();
  process.exit(1);
});
```

---

### Go

#### 2c. Install dependencies

```bash
mkdir my-cnc-app && cd my-cnc-app
go mod init my-cnc-app
go get github.com/dougcalobrisi/linuxcnc-grpc
go get google.golang.org/grpc
```

#### 3c. Get machine status

Create `main.go`:

```go
package main

import (
    "context"
    "fmt"
    "log"
    "time"

    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"

    pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

func main() {
    // Connect to your LinuxCNC machine
    conn, err := grpc.NewClient("LINUXCNC_IP:50051",
        grpc.WithTransportCredentials(insecure.NewCredentials()))
    if err != nil {
        log.Fatalf("Failed to connect: %v", err)
    }
    defer conn.Close()

    client := pb.NewLinuxCNCServiceClient(conn)

    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()

    status, err := client.GetStatus(ctx, &pb.GetStatusRequest{})
    if err != nil {
        log.Fatalf("GetStatus failed: %v", err)
    }

    // Print position
    pos := status.Position.ActualPosition
    fmt.Printf("Position: X=%.4f Y=%.4f Z=%.4f\n", pos.X, pos.Y, pos.Z)

    // Print machine state
    fmt.Printf("Mode:  %s\n", status.Task.TaskMode.String())
    fmt.Printf("State: %s\n", status.Task.TaskState.String())
}
```

Run it:

```bash
go run main.go
```

#### 4c. Stream real-time position updates

Create `stream.go`:

```go
package main

import (
    "context"
    "fmt"
    "io"
    "log"
    "os"
    "os/signal"
    "syscall"

    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"

    pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

func main() {
    conn, err := grpc.NewClient("LINUXCNC_IP:50051",
        grpc.WithTransportCredentials(insecure.NewCredentials()))
    if err != nil {
        log.Fatalf("Failed to connect: %v", err)
    }
    defer conn.Close()

    client := pb.NewLinuxCNCServiceClient(conn)

    // Cancel on Ctrl+C
    ctx, cancel := context.WithCancel(context.Background())
    sigChan := make(chan os.Signal, 1)
    signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
    go func() {
        <-sigChan
        cancel()
    }()

    stream, err := client.StreamStatus(ctx, &pb.StreamStatusRequest{
        IntervalMs: 100, // 10 updates per second
    })
    if err != nil {
        log.Fatalf("StreamStatus failed: %v", err)
    }

    fmt.Println("Streaming status... Press Ctrl+C to stop")

    for {
        status, err := stream.Recv()
        if err == io.EOF {
            break
        }
        if err != nil {
            if ctx.Err() != nil {
                break // Normal cancellation
            }
            log.Fatalf("Stream error: %v", err)
        }

        pos := status.Position.ActualPosition
        fmt.Printf("\rX=%8.3f Y=%8.3f Z=%8.3f  ", pos.X, pos.Y, pos.Z)
    }

    fmt.Println("\nStopped.")
}
```

#### 5c. Send commands (MDI G-code)

Create `mdi.go`:

```go
package main

import (
    "context"
    "fmt"
    "log"
    "time"

    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"

    pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

func main() {
    conn, err := grpc.NewClient("LINUXCNC_IP:50051",
        grpc.WithTransportCredentials(insecure.NewCredentials()))
    if err != nil {
        log.Fatalf("Failed to connect: %v", err)
    }
    defer conn.Close()

    client := pb.NewLinuxCNCServiceClient(conn)
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    serial := int32(0)
    nextSerial := func() int32 {
        serial++
        return serial
    }

    sendCmd := func(cmd *pb.LinuxCNCCommand) *pb.CommandResponse {
        cmd.Serial = nextSerial()
        cmd.Timestamp = time.Now().UnixNano()
        resp, err := client.SendCommand(ctx, cmd)
        if err != nil {
            log.Fatalf("SendCommand failed: %v", err)
        }
        return resp
    }

    // Power on and switch to MDI mode
    sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_State{
        State: &pb.StateCommand{State: pb.TaskState_STATE_ESTOP_RESET},
    }})
    time.Sleep(100 * time.Millisecond)

    sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_State{
        State: &pb.StateCommand{State: pb.TaskState_STATE_ON},
    }})
    time.Sleep(100 * time.Millisecond)

    sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_Mode{
        Mode: &pb.ModeCommand{Mode: pb.TaskMode_MODE_MDI},
    }})
    time.Sleep(100 * time.Millisecond)

    // Send G-code
    mdiCmd := &pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_Mdi{
        Mdi: &pb.MdiCommand{Command: "G0 X10 Y10"},
    }}
    resp := sendCmd(mdiCmd)

    if resp.Status == pb.RcsStatus_RCS_ERROR {
        log.Fatalf("Error: %s", resp.ErrorMessage)
    }

    // Wait for completion
    fmt.Println("Waiting for completion...")
    waitResp, err := client.WaitComplete(ctx, &pb.WaitCompleteRequest{
        Serial:  mdiCmd.Serial,
        Timeout: 30.0,
    })
    if err != nil {
        log.Fatalf("WaitComplete failed: %v", err)
    }
    fmt.Printf("Command completed: %s\n", waitResp.Status.String())
}
```

---

### Rust

#### 2d. Install dependencies

```bash
cargo new my-cnc-app && cd my-cnc-app
```

Add to `Cargo.toml`:

```toml
[dependencies]
linuxcnc-grpc = "1.0"
tokio = { version = "1", features = ["rt-multi-thread", "macros", "signal"] }
tonic = "0.12"
futures-util = "0.3"
```

#### 3d. Get machine status

Replace `src/main.rs`:

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{GetStatusRequest, TaskMode, TaskState};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Connect to your LinuxCNC machine
    let mut client = LinuxCncServiceClient::connect("http://LINUXCNC_IP:50051").await?;

    let response = client
        .get_status(tonic::Request::new(GetStatusRequest {}))
        .await?;
    let status = response.into_inner();

    // Print position
    if let Some(position) = &status.position {
        if let Some(pos) = &position.actual_position {
            println!("Position: X={:.4} Y={:.4} Z={:.4}", pos.x, pos.y, pos.z);
        }
    }

    // Print machine state
    if let Some(task) = &status.task {
        let mode = match TaskMode::try_from(task.task_mode) {
            Ok(m) => format!("{:?}", m),
            Err(_) => "UNKNOWN".to_string(),
        };
        let state = match TaskState::try_from(task.task_state) {
            Ok(s) => format!("{:?}", s),
            Err(_) => "UNKNOWN".to_string(),
        };
        println!("Mode:  {mode}");
        println!("State: {state}");
    }

    Ok(())
}
```

Run it:

```bash
cargo run
```

#### 4d. Stream real-time position updates

Replace `src/main.rs`:

```rust
use futures_util::StreamExt;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::StreamStatusRequest;
use std::io::{self, Write};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut client = LinuxCncServiceClient::connect("http://LINUXCNC_IP:50051").await?;

    let request = tonic::Request::new(StreamStatusRequest { interval_ms: 100 });
    let mut stream = client.stream_status(request).await?.into_inner();

    println!("Streaming status... Press Ctrl+C to stop");

    while let Some(result) = stream.next().await {
        match result {
            Ok(status) => {
                if let Some(pos) = status
                    .position
                    .as_ref()
                    .and_then(|p| p.actual_position.as_ref())
                {
                    print!("\rX={:8.3} Y={:8.3} Z={:8.3}  ", pos.x, pos.y, pos.z);
                    io::stdout().flush()?;
                }
            }
            Err(e) => {
                eprintln!("\nStream error: {e}");
                break;
            }
        }
    }

    println!("\nStopped.");
    Ok(())
}
```

#### 5d. Send commands (MDI G-code)

Replace `src/main.rs`:

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    linux_cnc_command::Command, LinuxCncCommand, MdiCommand, ModeCommand, StateCommand,
    TaskMode, TaskState, WaitCompleteRequest,
};
use std::time::{SystemTime, UNIX_EPOCH};

fn timestamp_ns() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as i64
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut client = LinuxCncServiceClient::connect("http://LINUXCNC_IP:50051").await?;

    let mut serial: i32 = 0;
    let mut next_serial = || {
        serial += 1;
        serial
    };

    // Power on and switch to MDI mode
    let s = next_serial();
    client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: s,
            timestamp: timestamp_ns(),
            command: Some(Command::State(StateCommand {
                state: TaskState::StateEstopReset.into(),
            })),
        }))
        .await?;
    tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

    let s = next_serial();
    client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: s,
            timestamp: timestamp_ns(),
            command: Some(Command::State(StateCommand {
                state: TaskState::StateOn.into(),
            })),
        }))
        .await?;
    tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

    let s = next_serial();
    client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: s,
            timestamp: timestamp_ns(),
            command: Some(Command::Mode(ModeCommand {
                mode: TaskMode::ModeMdi.into(),
            })),
        }))
        .await?;
    tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

    // Send G-code
    let mdi_serial = next_serial();
    let response = client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: mdi_serial,
            timestamp: timestamp_ns(),
            command: Some(Command::Mdi(MdiCommand {
                command: "G0 X10 Y10".to_string(),
            })),
        }))
        .await?
        .into_inner();

    if response.status == 3 {
        // RCS_ERROR
        eprintln!("Error: {}", response.error_message);
        return Ok(());
    }

    // Wait for completion
    println!("Waiting for completion...");
    let result = client
        .wait_complete(tonic::Request::new(WaitCompleteRequest {
            serial: mdi_serial,
            timeout: 30.0,
        }))
        .await?
        .into_inner();

    println!("Command completed: status={}", result.status);
    Ok(())
}
```

---

## What's Next

- See the full [API Reference](api-reference.md) for all available RPCs and message types
- Check out the [Examples Guide](examples.md) for more advanced examples (jogging, HAL queries)
- Read [Server Configuration](server.md) for production setup (auto-start, TLS)
- Browse the complete example code in the [`examples/`](../examples/) directory

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `UNAVAILABLE: failed to connect` | Check that the gRPC server is running and `LINUXCNC_IP:50051` is reachable (try `telnet LINUXCNC_IP 50051`) |
| `UNAVAILABLE: linuxcnc not running` | Start LinuxCNC on the server machine before starting the gRPC server |
| `FAILED_PRECONDITION` on MDI commands | Machine must be powered on and in MDI mode first (see Step 5 examples) |
| `INVALID_ARGUMENT` | Check command parameters (e.g., axis index, spindle index) |
| Connection timeout | Ensure no firewall is blocking port 50051 between the two machines |
