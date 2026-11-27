# LinuxCNC gRPC Examples

Example clients demonstrating how to connect to and use the LinuxCNC gRPC server in multiple programming languages.

## Available Languages

| Language | Directory | Package |
|----------|-----------|---------|
| Python | [python/](python/) | `linuxcnc-grpc` (PyPI) |
| Go | [go/](go/) | `github.com/dougcalobrisi/linuxcnc-grpc` |
| Node.js/TypeScript | [node/](node/) | `linuxcnc-grpc` (npm) |
| Rust | [rust/](rust/) | `linuxcnc-grpc` (crates.io) |

## Examples

Each language directory contains equivalent implementations of these examples:

| Example | Description |
|---------|-------------|
| `get_status` | Connect and poll machine status (simplest example) |
| `stream_status` | Stream real-time status updates for dashboards |
| `jog_axis` | Jog an axis (continuous and incremental movements) |
| `mdi_command` | Execute MDI G-code commands with interactive mode |
| `hal_query` | Query HAL pins, signals, parameters, and components |
| `upload_file` | Upload, list, and delete G-code files |

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

## Quick Start

### Python
```bash
pip install linuxcnc-grpc
cd examples/python
python get_status.py
```

### Go
```bash
cd examples/go
go run ./cmd/get_status
```

### Node.js/TypeScript
```bash
cd examples/node
npm install
npx tsx get_status.ts
```

### Rust
```bash
cd examples/rust
cargo run --bin get_status
```

## Prerequisites

All examples require:

1. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc
   ```

2. Network access to the gRPC server (default: localhost:50051)

## Connection Options

All examples support connection configuration:

```bash
# Python
python get_status.py --host 192.168.1.100 --port 50051

# Go
go run ./cmd/get_status --host 192.168.1.100 --port 50051

# Node.js
npx tsx get_status.ts --host 192.168.1.100 --port 50051

# Rust
cargo run --bin get_status -- --host 192.168.1.100 --port 50051
```

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**

## See Also

- [Python README](python/README.md) - Python-specific setup and usage
- [Go README](go/README.md) - Go-specific setup and usage
- [Node.js README](node/README.md) - Node.js/TypeScript-specific setup and usage
- [Rust README](rust/README.md) - Rust-specific setup and usage
