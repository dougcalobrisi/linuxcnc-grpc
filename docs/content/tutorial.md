---
title: "Step-by-Step Tutorial"
weight: 20
---

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

Start the server (it waits for LinuxCNC if LinuxCNC is not running yet):

```bash
linuxcnc-grpc --host 0.0.0.0 --port 50051
```

> `--host 0.0.0.0` makes the server listen on all network interfaces so remote clients can connect. See [Server Configuration](server.md) for auto-start setup, TLS, and advanced options.

Verify the server's port is reachable from your dev machine:

```bash
# From your dev machine
nc -zv LINUXCNC_IP 50051
```

This only shows that the port is open. The status call in Step 3 shows that the server answers and is connected to LinuxCNC. (`grpcurl ... list` does not work: the server does not offer gRPC reflection.)

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

LinuxCNC runs MDI moves only on a machine that is powered on, **homed** and in MDI mode. This script powers the machine on and switches to MDI mode, waiting for each step with `WaitComplete` rather than sleeping. It does not home the machine: home it first, from the GUI or with the `home` command. On an unhomed machine LinuxCNC refuses the move ("Can't issue MDI command when not homed"). The server never switches modes for you; see [Modes and teleop: what to send first](api-reference.md#modes-and-teleop-what-to-send-first).

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

def wait_for(sent, step, timeout=5.0):
    """Stop unless `sent` (a SendCommand answer) was dispatched and then finishes."""
    if sent.status != linuxcnc_pb2.RCS_DONE:
        raise SystemExit(f"{step} was not sent: {sent.error_message}")
    request = linuxcnc_pb2.WaitCompleteRequest(timeout=timeout)
    if sent.HasField("linuxcnc_serial"):
        request.linuxcnc_serial = sent.linuxcnc_serial
    else:
        print("Note: no linuxcnc_serial; waiting on the latest command sent by any client")
    result = stub.WaitComplete(request)
    if result.status != linuxcnc_pb2.RCS_DONE:
        # RCS_EXEC: the wait timed out. RCS_ERROR: the command failed.
        status_name = linuxcnc_pb2.RcsStatus.Name(result.status)
        raise SystemExit(f"{step} did not finish: {status_name} {result.error_message}")

# Power on and switch to MDI mode, waiting for each step
wait_for(set_state(linuxcnc_pb2.STATE_ESTOP_RESET), "E-stop reset")
wait_for(set_state(linuxcnc_pb2.STATE_ON), "Machine on")
wait_for(set_mode(linuxcnc_pb2.MODE_MDI), "MDI mode")

# Send a G-code command (the machine must be homed)
cmd = linuxcnc_pb2.LinuxCNCCommand()
cmd.serial = 3
cmd.timestamp = int(time.time() * 1e9)
cmd.mdi.command = "G0 X10 Y10"
wait_for(stub.SendCommand(cmd), "G0 X10 Y10", timeout=30.0)
print("Command completed")

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

LinuxCNC runs MDI moves only on a machine that is powered on, **homed** and in MDI mode. This script powers the machine on and switches to MDI mode, waiting for each step with `WaitComplete` rather than sleeping. It does not home the machine: home it first, from the GUI or with the `home` command. On an unhomed machine LinuxCNC refuses the move ("Can't issue MDI command when not homed"). The server never switches modes for you; see [Modes and teleop: what to send first](api-reference.md#modes-and-teleop-what-to-send-first).

Create `mdi.ts`:

```typescript
import { Metadata } from "@grpc/grpc-js";
import {
  LinuxCNCServiceClient,
  LinuxCNCCommand,
  CommandResponse,
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

function sendCommand(cmd: LinuxCNCCommand): Promise<CommandResponse> {
  cmd.timestamp = Date.now() * 1000000;
  return new Promise((resolve, reject) => {
    client.sendCommand(cmd, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, resp) => {
      if (err) reject(err);
      else resolve(resp);
    });
  });
}

// Throw unless `sent` (a sendCommand answer) was dispatched and then finishes.
async function waitFor(sent: CommandResponse, step: string, timeout: number): Promise<void> {
  if (sent.status !== RcsStatus.RCS_DONE) {
    throw new Error(`${step} was not sent: ${sent.errorMessage}`);
  }
  if (sent.linuxcncSerial === undefined) {
    console.log("Note: no linuxcncSerial; waiting on the latest command sent by any client");
  }
  const request = WaitCompleteRequest.create({ timeout, linuxcncSerial: sent.linuxcncSerial });
  const deadline = new Date(Date.now() + (timeout * 1000) + 5000);
  const result = await new Promise<CommandResponse>((resolve, reject) => {
    client.waitComplete(request, new Metadata(), { deadline }, (err, resp) => {
      if (err) reject(err);
      else resolve(resp);
    });
  });
  if (result.status !== RcsStatus.RCS_DONE) {
    // RCS_EXEC: the wait timed out. RCS_ERROR: the command failed.
    throw new Error(`${step} did not finish: ${RcsStatus[result.status]} ${result.errorMessage}`);
  }
}

async function main() {
  // Power on and switch to MDI mode, waiting for each step
  let cmd = LinuxCNCCommand.create({ serial: 1, state: { state: TaskState.STATE_ESTOP_RESET } });
  await waitFor(await sendCommand(cmd), "E-stop reset", 5.0);

  cmd = LinuxCNCCommand.create({ serial: 2, state: { state: TaskState.STATE_ON } });
  await waitFor(await sendCommand(cmd), "Machine on", 5.0);

  cmd = LinuxCNCCommand.create({ serial: 3, mode: { mode: TaskMode.MODE_MDI } });
  await waitFor(await sendCommand(cmd), "MDI mode", 5.0);

  // Send G-code (the machine must be homed)
  cmd = LinuxCNCCommand.create({ serial: 4, mdi: { command: "G0 X10 Y10" } });
  await waitFor(await sendCommand(cmd), "G0 X10 Y10", 30.0);
  console.log("Command completed");

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

LinuxCNC runs MDI moves only on a machine that is powered on, **homed** and in MDI mode. This script powers the machine on and switches to MDI mode, waiting for each step with `WaitComplete` rather than sleeping. It does not home the machine: home it first, from the GUI or with the `home` command. On an unhomed machine LinuxCNC refuses the move ("Can't issue MDI command when not homed"). The server never switches modes for you; see [Modes and teleop: what to send first](api-reference.md#modes-and-teleop-what-to-send-first).

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
    ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
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

    // Stop unless sent (a SendCommand answer) was dispatched and then finishes.
    waitFor := func(sent *pb.CommandResponse, step string, timeout float64) {
        if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
            log.Fatalf("%s was not sent: %s", step, sent.GetErrorMessage())
        }
        if sent.LinuxcncSerial == nil {
            fmt.Println("Note: no LinuxcncSerial; waiting on the latest command sent by any client")
        }
        result, err := client.WaitComplete(ctx, &pb.WaitCompleteRequest{
            Timeout:        timeout,
            LinuxcncSerial: sent.LinuxcncSerial,
        })
        if err != nil {
            log.Fatalf("WaitComplete failed: %v", err)
        }
        if result.GetStatus() != pb.RcsStatus_RCS_DONE {
            // RCS_EXEC: the wait timed out. RCS_ERROR: the command failed.
            log.Fatalf("%s did not finish: %s %s", step, result.GetStatus(), result.GetErrorMessage())
        }
    }

    // Power on and switch to MDI mode, waiting for each step
    waitFor(sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_State{
        State: &pb.StateCommand{State: pb.TaskState_STATE_ESTOP_RESET},
    }}), "E-stop reset", 5.0)

    waitFor(sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_State{
        State: &pb.StateCommand{State: pb.TaskState_STATE_ON},
    }}), "Machine on", 5.0)

    waitFor(sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_Mode{
        Mode: &pb.ModeCommand{Mode: pb.TaskMode_MODE_MDI},
    }}), "MDI mode", 5.0)

    // Send G-code (the machine must be homed)
    waitFor(sendCmd(&pb.LinuxCNCCommand{Command: &pb.LinuxCNCCommand_Mdi{
        Mdi: &pb.MdiCommand{Command: "G0 X10 Y10"},
    }}), "G0 X10 Y10", 30.0)
    fmt.Println("Command completed")
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

LinuxCNC runs MDI moves only on a machine that is powered on, **homed** and in MDI mode. This script powers the machine on and switches to MDI mode, waiting for each step with `WaitComplete` rather than sleeping. It does not home the machine: home it first, from the GUI or with the `home` command. On an unhomed machine LinuxCNC refuses the move ("Can't issue MDI command when not homed"). The server never switches modes for you; see [Modes and teleop: what to send first](api-reference.md#modes-and-teleop-what-to-send-first).

Replace `src/main.rs`:

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    linux_cnc_command::Command, CommandResponse, LinuxCncCommand, MdiCommand, ModeCommand,
    RcsStatus, StateCommand, TaskMode, TaskState, WaitCompleteRequest,
};
use std::time::{SystemTime, UNIX_EPOCH};
use tonic::transport::Channel;

fn timestamp_ns() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as i64
}

// Fail unless `sent` (a send_command answer) was dispatched and then finishes.
async fn wait_for(
    client: &mut LinuxCncServiceClient<Channel>,
    sent: &CommandResponse,
    step: &str,
    timeout: f64,
) -> Result<(), Box<dyn std::error::Error>> {
    if sent.status() != RcsStatus::RcsDone {
        return Err(format!("{step} was not sent: {}", sent.error_message).into());
    }
    if sent.linuxcnc_serial.is_none() {
        println!("Note: no linuxcnc_serial; waiting on the latest command sent by any client");
    }
    let request = WaitCompleteRequest {
        timeout,
        linuxcnc_serial: sent.linuxcnc_serial,
        ..Default::default()
    };
    let result = client
        .wait_complete(tonic::Request::new(request))
        .await?
        .into_inner();
    if result.status() != RcsStatus::RcsDone {
        // RCS_EXEC: the wait timed out. RCS_ERROR: the command failed.
        let status_name = result.status().as_str_name();
        return Err(format!("{step} did not finish: {status_name} {}", result.error_message).into());
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut client = LinuxCncServiceClient::connect("http://LINUXCNC_IP:50051").await?;

    let mut serial: i32 = 0;
    let mut next_serial = || {
        serial += 1;
        serial
    };

    // Power on and switch to MDI mode, waiting for each step
    let s = next_serial();
    let sent = client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: s,
            timestamp: timestamp_ns(),
            command: Some(Command::State(StateCommand {
                state: TaskState::StateEstopReset.into(),
            })),
        }))
        .await?
        .into_inner();
    wait_for(&mut client, &sent, "E-stop reset", 5.0).await?;

    let s = next_serial();
    let sent = client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: s,
            timestamp: timestamp_ns(),
            command: Some(Command::State(StateCommand {
                state: TaskState::StateOn.into(),
            })),
        }))
        .await?
        .into_inner();
    wait_for(&mut client, &sent, "Machine on", 5.0).await?;

    let s = next_serial();
    let sent = client
        .send_command(tonic::Request::new(LinuxCncCommand {
            serial: s,
            timestamp: timestamp_ns(),
            command: Some(Command::Mode(ModeCommand {
                mode: TaskMode::ModeMdi.into(),
            })),
        }))
        .await?
        .into_inner();
    wait_for(&mut client, &sent, "MDI mode", 5.0).await?;

    // Send G-code (the machine must be homed)
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

    wait_for(&mut client, &response, "G0 X10 Y10", 30.0).await?;
    println!("Command completed");
    Ok(())
}
```

---

## What's Next

- See the full [API Reference](api-reference.md) for all available RPCs and message types
- Check out the [Examples Guide](examples.md) for more advanced examples (jogging, HAL queries)
- Read [Server Configuration](server.md) for production setup (auto-start, TLS)
- Browse the complete example code in the [`examples/`](https://github.com/dougcalobrisi/linuxcnc-grpc/tree/main/examples) directory

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `UNAVAILABLE: failed to connect` | Check that the gRPC server is running and `LINUXCNC_IP:50051` is reachable (try `telnet LINUXCNC_IP 50051`) |
| `UNAVAILABLE: LinuxCNC is not running…` | The server is up but LinuxCNC is not. Start LinuxCNC; the server connects within a few seconds |
| MDI command has no effect, or `WaitComplete` returns `RCS_ERROR` | Machine must be powered on, homed and in MDI mode first (see Step 5 examples and [Modes and teleop](api-reference.md#modes-and-teleop-what-to-send-first)). `SendCommand` still answers `RCS_DONE`, which only means the command was sent; LinuxCNC reports its refusal as an operator error message (`StreamErrors` or `status.errors`) |
| `SendCommand` returns `RCS_ERROR` | Read `error_message`. Either the server rejected the command before sending it (for example a joint or spindle index out of range or an empty MDI command), or a LinuxCNC call failed. After an error from `coolant`, `set_limit`, `override_config` or `program_options`, check the status before retrying: part of the command may have been sent (see [`RCS_ERROR` from `SendCommand`](server.md#rcs_error-from-sendcommand)). This is a normal response, not a gRPC error |
| `INVALID_ARGUMENT` | A file RPC was given a bad file name (outside the NC files directory, empty) or bad content |
| Connection timeout | Ensure no firewall is blocking port 50051 between the two machines |
