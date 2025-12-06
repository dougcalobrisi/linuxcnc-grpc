# LinuxCNC gRPC Server Examples

Example Python clients demonstrating how to connect to and use the LinuxCNC gRPC server.

## Prerequisites

1. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc-server
   ```

2. Install the package (recommended) or just the gRPC libraries:
   ```bash
   # Option A: Install the package (includes protobuf stubs)
   pip install -e /path/to/linuxcnc-grpc-server

   # Option B: Install dependencies only (requires manual stub setup)
   pip install grpcio protobuf
   ```

## Setting Up the Client

The examples automatically try to import from the installed `linuxcnc-grpc-server` package first, falling back to a local `pb/` directory if not installed.

**If you installed the package (Option A above):** Examples will work immediately.

**If using standalone dependencies (Option B):** Copy the pre-generated stubs:
```bash
# From the examples/ directory
mkdir -p pb
cp ../src/linuxcnc_grpc_server/_generated/*.py pb/
touch pb/__init__.py
```

## Examples

| Example | Description |
|---------|-------------|
| `get_status.py` | Connect and poll machine status |
| `jog_axis.py` | Jog an axis with keyboard control |
| `mdi_command.py` | Execute MDI commands (G-code) |
| `stream_status.py` | Stream real-time status updates |
| `hal_query.py` | Query HAL pins, signals, and parameters |

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**
