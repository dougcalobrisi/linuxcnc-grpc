# Python Examples

Example Python clients demonstrating how to connect to and use the LinuxCNC gRPC server.

## Prerequisites

1. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc
   ```

2. Install the package (recommended) or just the gRPC libraries:
   ```bash
   # Option A: Install the package (includes protobuf stubs)
   pip install linuxcnc-grpc

   # Option B: Install from source
   pip install -e /path/to/linuxcnc-grpc
   ```

## Examples

| Example | Description |
|---------|-------------|
| `get_status.py` | Connect and poll machine status |
| `stream_status.py` | Stream real-time status updates |
| `jog_axis.py` | Jog an axis (continuous and incremental) |
| `mdi_command.py` | Execute MDI commands (G-code) |
| `hal_query.py` | Query HAL pins, signals, and parameters |

## Running Examples

```bash
# Basic status
python get_status.py

# Stream status updates
python stream_status.py --interval 100

# Jog demo (moves the machine!)
python jog_axis.py

# MDI commands
python mdi_command.py "G0 X10 Y10"
python mdi_command.py --interactive

# HAL queries
python hal_query.py pins "axis.*"
python hal_query.py signals
python hal_query.py components
python hal_query.py watch spindle.0.speed-out axis.x.pos-cmd
```

## Connection Options

All examples support `--host` and `--port` options:

```bash
python get_status.py --host 192.168.1.100 --port 50051
```

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**
