/**
 * HAL Query Example
 *
 * Query HAL (Hardware Abstraction Layer) pins, signals, and parameters.
 * Useful for debugging HAL configurations and monitoring I/O.
 *
 * Usage:
 *   npx tsx hal_query.ts pins "axis.*"
 *   npx tsx hal_query.ts signals
 *   npx tsx hal_query.ts components
 *   npx tsx hal_query.ts watch "spindle.0.speed-out" "axis.x.pos-cmd"
 */

import { program } from "commander";
import {
  HalServiceClient,
  GetSystemStatusRequest,
  Metadata,
  QueryPinsCommand,
  QuerySignalsCommand,
  QueryParamsCommand,
  QueryComponentsCommand,
  WatchRequest,
  HalValue,
  HalType,
  PinDirection,
  ParamDirection,
  halTypeToJSON,
  pinDirectionToJSON,
  credentials,
  status,
} from "linuxcnc-grpc";
import type { ServiceError } from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("--interval <interval>", "Update interval in ms for watch command", "500");

program
  .command("pins [pattern]")
  .description("Query HAL pins")
  .action((pattern) => runCommand("pins", pattern || "*"));

program
  .command("signals [pattern]")
  .description("Query HAL signals")
  .action((pattern) => runCommand("signals", pattern || "*"));

program
  .command("params [pattern]")
  .description("Query HAL parameters")
  .action((pattern) => runCommand("params", pattern || "*"));

program
  .command("components [pattern]")
  .description("Query HAL components")
  .action((pattern) => runCommand("components", pattern || "*"));

program
  .command("watch <names...>")
  .description("Watch values for changes")
  .action((names) => runCommand("watch", names));

program
  .command("status")
  .description("Get HAL system status")
  .action(() => runCommand("status", ""));

const RPC_DEADLINE = 5000;

// Declare client before parse() since actions may run synchronously during parse()
let client: HalServiceClient;

program.parse();

function getAddress(): string {
  const opts = program.opts();
  return `${opts.host}:${opts.port}`;
}

function getIntervalMs(): number {
  const opts = program.opts();
  return parseInt(opts.interval);
}

function formatValue(value: HalValue | undefined): string {
  if (!value) return "?";
  if (value.bitValue !== undefined) {
    return value.bitValue ? "TRUE" : "FALSE";
  }
  if (value.floatValue !== undefined) {
    return value.floatValue.toPrecision(6);
  }
  if (value.s32Value !== undefined) {
    return value.s32Value.toString();
  }
  if (value.u32Value !== undefined) {
    return value.u32Value.toString();
  }
  if (value.s64Value !== undefined) {
    return value.s64Value.toString();
  }
  if (value.u64Value !== undefined) {
    return value.u64Value.toString();
  }
  if (value.portValue !== undefined) {
    return value.portValue;
  }
  return "?";
}

function formatType(halType: HalType): string {
  return halTypeToJSON(halType).replace("HAL_", "");
}

function formatDirection(direction: PinDirection): string {
  return pinDirectionToJSON(direction)
    .replace("HAL_", "")
    .replace("PIN_DIR_", "");
}

function queryPins(pattern: string): void {
  const request = QueryPinsCommand.create();
  request.pattern = pattern;

  client.queryPins(request, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, response) => {
    if (err) {
      console.error(`Error: ${err.message}`);
      client.close();
      process.exit(1);
    }
    if (!response.success) {
      console.error(`Error: ${response.error}`);
      process.exit(1);
    }

    console.log(`Found ${response.pins.length} pins matching '${pattern}':\n`);
    console.log(
      `${"Name".padEnd(50)} ${"Type".padEnd(6)} ${"Dir".padEnd(4)} ${"Value".padEnd(15)} Signal`
    );
    console.log("-".repeat(90));

    const pins = [...response.pins].sort((a, b) => a.name.localeCompare(b.name));
    for (const pin of pins) {
      const direction = formatDirection(pin.direction);
      const value = formatValue(pin.value);
      const pinType = formatType(pin.type);
      const signal = pin.signal || "-";
      console.log(
        `${pin.name.padEnd(50)} ${pinType.padEnd(6)} ${direction.padEnd(4)} ${value.padEnd(15)} ${signal}`
      );
    }
    client.close();
  });
}

function querySignals(pattern: string): void {
  const request = QuerySignalsCommand.create();
  request.pattern = pattern;

  client.querySignals(request, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, response) => {
    if (err) {
      console.error(`Error: ${err.message}`);
      client.close();
      process.exit(1);
    }
    if (!response.success) {
      console.error(`Error: ${response.error}`);
      process.exit(1);
    }

    console.log(`Found ${response.signals.length} signals matching '${pattern}':\n`);
    console.log(
      `${"Name".padEnd(40)} ${"Type".padEnd(6)} ${"Value".padEnd(15)} ${"Driver".padEnd(30)} Readers`
    );
    console.log("-".repeat(100));

    const signals = [...response.signals].sort((a, b) =>
      a.name.localeCompare(b.name)
    );
    for (const sig of signals) {
      const value = formatValue(sig.value);
      const sigType = formatType(sig.type);
      const driver = sig.driver || "(none)";
      const readers = sig.readerCount > 0 ? `${sig.readerCount} readers` : "-";
      console.log(
        `${sig.name.padEnd(40)} ${sigType.padEnd(6)} ${value.padEnd(15)} ${driver.padEnd(30)} ${readers}`
      );
    }
    client.close();
  });
}

function queryParams(pattern: string): void {
  const request = QueryParamsCommand.create();
  request.pattern = pattern;

  client.queryParams(request, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, response) => {
    if (err) {
      console.error(`Error: ${err.message}`);
      client.close();
      process.exit(1);
    }
    if (!response.success) {
      console.error(`Error: ${response.error}`);
      process.exit(1);
    }

    console.log(`Found ${response.params.length} parameters matching '${pattern}':\n`);
    console.log(
      `${"Name".padEnd(50)} ${"Type".padEnd(6)} ${"Mode".padEnd(4)} Value`
    );
    console.log("-".repeat(80));

    const params = [...response.params].sort((a, b) =>
      a.name.localeCompare(b.name)
    );
    for (const param of params) {
      const value = formatValue(param.value);
      const paramType = formatType(param.type);
      const mode = param.direction === ParamDirection.HAL_RW ? "RW" : "RO";
      console.log(
        `${param.name.padEnd(50)} ${paramType.padEnd(6)} ${mode.padEnd(4)} ${value}`
      );
    }
    client.close();
  });
}

function queryComponents(pattern: string): void {
  const request = QueryComponentsCommand.create();
  request.pattern = pattern;

  client.queryComponents(request, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, response) => {
    if (err) {
      console.error(`Error: ${err.message}`);
      client.close();
      process.exit(1);
    }
    if (!response.success) {
      console.error(`Error: ${response.error}`);
      process.exit(1);
    }

    console.log(`Found ${response.components.length} components matching '${pattern}':\n`);
    console.log(
      `${"Name".padEnd(30)} ${"ID".padEnd(6)} ${"Ready".padEnd(6)} ${"Pins".padEnd(6)} Params`
    );
    console.log("-".repeat(60));

    const comps = [...response.components].sort((a, b) =>
      a.name.localeCompare(b.name)
    );
    for (const comp of comps) {
      const ready = comp.ready ? "Yes" : "No";
      console.log(
        `${comp.name.padEnd(30)} ${comp.id.toString().padEnd(6)} ${ready.padEnd(6)} ${comp.pins.length.toString().padEnd(6)} ${comp.params.length}`
      );
    }
    client.close();
  });
}

function watchValues(names: string[]): void {
  const request = WatchRequest.create();
  request.names = names;
  request.intervalMs = getIntervalMs();

  console.log(`Watching ${names.length} values (interval: ${getIntervalMs()}ms)`);
  console.log("Press Ctrl+C to stop\n");

  const stream = client.watchValues(request);

  stream.on("data", (batch) => {
    for (const change of batch.changes) {
      const oldVal = formatValue(change.oldValue);
      const newVal = formatValue(change.newValue);
      const ts = new Date(Number(change.timestamp) / 1000000).toLocaleTimeString();
      console.log(`[${ts}] ${change.name}: ${oldVal} -> ${newVal}`);
    }
  });

  stream.on("error", (err: ServiceError) => {
    if (err.code === status.CANCELLED) {
      // Normal cancellation
    } else {
      console.error(`\ngRPC error: ${err.code}: ${err.details}`);
      client.close();
      process.exit(1);
    }
  });

  stream.on("end", () => {
    client.close();
  });

  process.on("SIGINT", () => {
    stream.cancel();
  });
}

function getSystemStatus(): void {
  client.getSystemStatus(GetSystemStatusRequest.create(), new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, status) => {
    if (err) {
      console.error(`Error: ${err.message}`);
      client.close();
      process.exit(1);
    }

    console.log("HAL System Status");
    console.log("=".repeat(40));
    console.log(`Pins:       ${status.pins.length}`);
    console.log(`Signals:    ${status.signals.length}`);
    console.log(`Parameters: ${status.params.length}`);
    console.log(`Components: ${status.components.length}`);
    console.log(`Simulation: ${status.isSim}`);
    console.log(`Real-time:  ${status.isRt}`);
    console.log(`Userspace:  ${status.isUserspace}`);
    if (status.kernelVersion) {
      console.log(`Kernel:     ${status.kernelVersion}`);
    }
    client.close();
  });
}

function runCommand(command: string, arg: string | string[]): void {
  const address = getAddress();
  console.error(`Connecting to ${address}...`);
  client = new HalServiceClient(address, credentials.createInsecure(), {
    'grpc.initial_reconnect_backoff_ms': 1000,
    'grpc.max_reconnect_backoff_ms': 5000,
  });

  switch (command) {
    case "pins":
      queryPins(arg as string);
      break;
    case "signals":
      querySignals(arg as string);
      break;
    case "params":
      queryParams(arg as string);
      break;
    case "components":
      queryComponents(arg as string);
      break;
    case "watch":
      watchValues(arg as string[]);
      break;
    case "status":
      getSystemStatus();
      break;
    default:
      console.error(`Unknown command: ${command}`);
      process.exit(1);
  }
}
