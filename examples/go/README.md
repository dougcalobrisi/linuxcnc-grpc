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
| `cmd/upload_file` | Upload, list, and delete G-code files |

## What the motion examples do and do not do

`jog_axis` and `mdi_command` move the machine, so they check before they act:

- The machine must already be on. They never reset E-stop or power the
  machine on; if it is not on they stop and say so.
- Every joint must already be homed. They never home the machine.
- They do switch the task mode (`jog_axis` to manual with teleop on,
  `mdi_command` to MDI), wait for the switch with `WaitComplete`, and then
  check that the status shows it.
- The interpreter must be idle. `jog_axis` checks before it changes mode or
  jogs, and `mdi_command` before every command, so nothing is sent on top of
  a running program or of an MDI command that has not finished.
- `jog_axis` sends the jog stop on every way out of the continuous jog: when
  its time is up, on Ctrl+C, after an error and when its output can no longer
  be written. The incremental jog gets a stop when it is interrupted or fails
  part way; when it completed normally and the status shows the axis moved
  and is standing still, no stop is sent. If a stop fails, the example says
  the axis may still be moving and exits 1.
- `mdi_command` reports `Done.` only when the command finished. A wait that
  runs out (`--wait-timeout`, 60 seconds by default) is reported as a timeout
  and the program exits non-zero; the command itself keeps running. In
  interactive mode a failed command does not end the session, but the exit
  code at the end is non-zero if any command failed.

`upload_file` never replaces a file: if the name is taken it stops (pick
another with `--filename`), and `--cleanup` deletes only the file it uploaded.

`stream_status` and `hal_query watch` exit 0 on Ctrl+C and non-zero when the
stream fails or the server cancels it. `stream_status` also gives up, and
exits non-zero, when no update arrives for 30 seconds, or for twice
`--interval` plus 5 seconds if that is longer. `--interval` is in
milliseconds, from 1 to 60000.

## Running Examples

From the examples/go directory:

```bash
# Basic status
go run ./cmd/get_status

# Stream status updates
go run ./cmd/stream_status --interval 100

# Jog demo (moves the machine!)
go run ./cmd/jog_axis

# MDI commands
go run ./cmd/mdi_command "G0 X10 Y10"
go run ./cmd/mdi_command --interactive

# HAL queries
go run ./cmd/hal_query pins "axis.*"
go run ./cmd/hal_query signals
go run ./cmd/hal_query components
go run ./cmd/hal_query watch spindle.0.speed-out axis.x.pos-cmd

# Upload a sample G-code file, list files, then delete the upload
go run ./cmd/upload_file --cleanup
```

## Connection Options

All examples support `--host` and `--port` flags:

```bash
go run ./cmd/get_status --host 192.168.1.100 --port 50051
```

Flags go before any other argument, and there is no `--` separator: Go's
`flag` package stops reading flags at `--` or at the first argument that is
not a flag. `go run ./cmd/mdi_command --no-wait "G0 X10"` works;
`go run ./cmd/mdi_command "G0 X10" --no-wait` is rejected.

## Using as a Library

To use the LinuxCNC gRPC client in your own Go project:

```bash
go get github.com/dougcalobrisi/linuxcnc-grpc
```

```go
import pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"

conn, err := grpc.NewClient("localhost:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
if err != nil {
    log.Fatal(err)
}
defer conn.Close()
client := pb.NewLinuxCNCServiceClient(conn)

// Give every call a deadline.
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()
status, err := client.GetStatus(ctx, &pb.GetStatusRequest{})
if err != nil {
    log.Fatal(err)
}
fmt.Println(status.GetTask().GetTaskMode())
```

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**
