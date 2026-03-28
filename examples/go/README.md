# Go Examples

Example Go clients demonstrating how to connect to and use the LinuxCNC gRPC server.

## Prerequisites

1. Go 1.24 or later installed
2. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc
   ```

## Examples

| Example | Description |
|---------|-------------|
| `cmd/get_status` | Connect and poll machine status |
| `cmd/stream_status` | Stream real-time status updates |
| `cmd/jog_axis` | Jog an axis (continuous and incremental) |
| `cmd/mdi_command` | Execute MDI commands (G-code) |
| `cmd/hal_query` | Query HAL pins, signals, and parameters |

## Running Examples

From the examples/go directory:

```bash
# Basic status
go run ./cmd/get_status

# Stream status updates
go run ./cmd/stream_status -- --interval 100

# Jog demo (moves the machine!)
go run ./cmd/jog_axis

# MDI commands
go run ./cmd/mdi_command "G0 X10 Y10"
go run ./cmd/mdi_command -- --interactive

# HAL queries
go run ./cmd/hal_query pins "axis.*"
go run ./cmd/hal_query signals
go run ./cmd/hal_query components
go run ./cmd/hal_query watch spindle.0.speed-out axis.x.pos-cmd
```

## Connection Options

All examples support `--host` and `--port` flags:

```bash
go run ./cmd/get_status -- --host 192.168.1.100 --port 50051
```

## Using as a Library

To use the LinuxCNC gRPC client in your own Go project:

```bash
go get github.com/dougcalobrisi/linuxcnc-grpc
```

```go
import pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"

conn, _ := grpc.NewClient("localhost:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
client := pb.NewLinuxCNCServiceClient(conn)
status, _ := client.GetStatus(context.Background(), &pb.GetStatusRequest{})
```

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**
