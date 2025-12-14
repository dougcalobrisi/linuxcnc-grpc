# LinuxCNC gRPC Examples

Example clients demonstrating how to connect to and use the LinuxCNC gRPC server in multiple programming languages.

## Available Languages

| Language | Directory | Package |
|----------|-----------|---------|
| Python | [python/](python/) | `linuxcnc-grpc` (PyPI) |
| Go | [go/](go/) | `github.com/dougcalobrisi/linuxcnc-grpc` |
| Node.js/TypeScript | [node/](node/) | `linuxcnc-grpc` (npm) |

## Examples

Each language directory contains equivalent implementations of these examples:

| Example | Description |
|---------|-------------|
| `get_status` | Connect and poll machine status (simplest example) |
| `stream_status` | Stream real-time status updates for dashboards |
| `jog_axis` | Jog an axis (continuous and incremental movements) |
| `mdi_command` | Execute MDI G-code commands with interactive mode |
| `hal_query` | Query HAL pins, signals, parameters, and components |

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

## Prerequisites

All examples require:

1. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc-server
   ```

2. Network access to the gRPC server (default: localhost:50051)

## Connection Options

All examples support connection configuration:

```bash
# Python
python get_status.py --host 192.168.1.100 --port 50051

# Go
go run ./cmd/get_status -- --host 192.168.1.100 --port 50051

# Node.js
npx tsx get_status.ts --host 192.168.1.100 --port 50051
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
