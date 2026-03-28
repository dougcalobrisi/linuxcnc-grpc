# Node.js/TypeScript Examples

Example TypeScript clients demonstrating how to connect to and use the LinuxCNC gRPC server.

## Prerequisites

1. Node.js 18 or later installed
2. LinuxCNC running with the gRPC server started:
   ```bash
   linuxcnc-grpc-server
   ```

## Examples

| Example | Description |
|---------|-------------|
| `get_status.ts` | Connect and poll machine status |
| `stream_status.ts` | Stream real-time status updates |
| `jog_axis.ts` | Jog an axis (continuous and incremental) |
| `mdi_command.ts` | Execute MDI commands (G-code) |
| `hal_query.ts` | Query HAL pins, signals, and parameters |

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
```

Or use npm scripts:

```bash
npm run get-status
npm run stream-status
npm run jog-axis
npm run mdi-command -- "G0 X10 Y10"
npm run hal-query -- pins "axis.*"
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
import { LinuxCNCServiceClient, GetStatusRequest, credentials } from 'linuxcnc-grpc';

const client = new LinuxCNCServiceClient(
  'localhost:50051',
  credentials.createInsecure()
);

client.getStatus(GetStatusRequest.create(), (err, status) => {
  if (err) {
    console.error('Error:', err);
    return;
  }
  console.log('Status:', status);
});
```

## Safety Warning

These examples can control real CNC machinery. Always ensure:

- E-stop is accessible and tested
- You understand what each command does before running
- The machine is in a safe state
- You're prepared to hit E-stop if something goes wrong

**Never run untested code on a machine with a workpiece or near people.**
