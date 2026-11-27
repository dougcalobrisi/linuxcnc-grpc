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
| `upload_file.py` | Upload, list, and delete G-code files |

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

# Upload a sample G-code file, list files, then delete the upload
python upload_file.py --cleanup
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
