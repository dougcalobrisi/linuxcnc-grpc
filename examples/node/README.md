# Node.js/TypeScript Examples

Example TypeScript clients demonstrating how to connect to and use the LinuxCNC gRPC server.

## Prerequisites

1. Node.js 18 or later installed
2. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc
   ```

## Examples

| Example | Description |
|---------|-------------|
| `get_status.ts` | Connect and poll machine status |
| `stream_status.ts` | Stream real-time status updates |
| `jog_axis.ts` | Jog an axis (continuous and incremental) |
| `mdi_command.ts` | Execute MDI commands (G-code) |
| `hal_query.ts` | Query HAL pins, signals, and parameters |
| `upload_file.ts` | Upload, list, and delete G-code files |

## What the motion examples do and do not do

`jog_axis` and `mdi_command` move the machine, so they check before they act:

- The machine must already be on. They never reset E-stop or power the
  machine on; if it is not on they stop and say so.
- Every joint must already be homed. They never home the machine.
- They do switch the task mode (`jog_axis` to manual with teleop on,
  `mdi_command` to MDI), wait for the switch with `WaitComplete`, and then
  check that the status shows it.
- The machine must be idle: the interpreter idle, `trajectory.inpos` true
  and `trajectory.current_vel` zero. The interpreter state alone is not
  enough; on the 2.9 simulator it read `INTERP_IDLE` for the whole of some
  MDI moves. `jog_axis` checks before it changes mode or jogs, and
  `mdi_command` before every command, so nothing is sent on top of a running
  program, a jog or an MDI command that has not finished.
- `jog_axis` repeats these checks on a fresh status right before each jog is
  started, so a machine that was put in E-stop, turned off or given a program
  after the first check gets no further jog. A jog stop is always sent.
- While `jog_axis` waits for an axis to stop it also watches the machine
  state. After an E-stop the velocity in the status keeps its last value, so
  the example does not wait for it to reach zero: it stops with the "machine
  is not on" message.
- `jog_axis` sends the jog stop on every way out of the continuous jog: when
  its time is up, on Ctrl+C, after an error and when its output can no longer
  be written. The incremental jog gets a stop when it is interrupted or fails
  part way; when it completed normally and the status shows the axis moved
  and is standing still, no stop is sent. If a stop fails, the example says
  the axis may still be moving and exits 1.
- `mdi_command` reports `Done.` only when the command finished. `RCS_DONE`
  from `WaitComplete` is not enough for that: the wait also ends when
  LinuxCNC takes a later command from any client, while the MDI move may
  still be running. So the example then reads the status until the
  machine is idle by the rule above, and says that the result
  is not confirmed if that does not show within the timeout. A wait that
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

First, install dependencies:

```bash
cd examples/node
npm install
```

Then run any example:

```bash
# Basic status
npx tsx get_status.ts

# Stream status updates
npx tsx stream_status.ts --interval 100

# Jog demo (moves the machine!)
npx tsx jog_axis.ts

# MDI commands
npx tsx mdi_command.ts "G0 X10 Y10"
npx tsx mdi_command.ts --interactive

# HAL queries
npx tsx hal_query.ts pins "axis.*"
npx tsx hal_query.ts signals
npx tsx hal_query.ts components
npx tsx hal_query.ts watch spindle.0.speed-out axis.x.pos-cmd

# Upload a sample G-code file, list files, then delete the upload
npx tsx upload_file.ts --cleanup
```

Or use npm scripts:

```bash
npm run get-status
npm run stream-status
npm run jog-axis
npm run mdi-command -- "G0 X10 Y10"
npm run hal-query -- pins "axis.*"
npm run upload-file -- --cleanup
```

## Connection Options

All examples support `--host` and `--port` options:

```bash
npx tsx get_status.ts --host 192.168.1.100 --port 50051
```

## Using as a Library

To use the LinuxCNC gRPC client in your own TypeScript/Node.js project:

```bash
npm install linuxcnc-grpc
```

```typescript
import { LinuxCNCServiceClient, GetStatusRequest, Metadata, credentials } from 'linuxcnc-grpc';

const client = new LinuxCNCServiceClient(
  'localhost:50051',
  credentials.createInsecure()
);

// Give every call a deadline.
const deadline = new Date(Date.now() + 5000);
client.getStatus(GetStatusRequest.create(), new Metadata(), { deadline }, (err, status) => {
  if (err) {
    console.error('Error:', err);
  } else {
    console.log('Status:', status);
  }
  client.close();
});
```

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**
