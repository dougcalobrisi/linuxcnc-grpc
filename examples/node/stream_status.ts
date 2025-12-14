/**
 * Stream Status Example
 *
 * Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
 * This is useful for building dashboards or monitoring applications.
 *
 * Usage:
 *   npx tsx stream_status.ts [--interval 100]
 *
 * Press Ctrl+C to stop streaming.
 */

import * as grpc from "@grpc/grpc-js";
import { program } from "commander";
import {
  LinuxCNCServiceClient,
  StreamStatusRequest,
  LinuxCNCStatus,
  taskModeToJSON,
  taskStateToJSON,
  interpStateToJSON,
} from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("--interval <interval>", "Update interval in milliseconds", "100")
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;
const intervalMs = parseInt(opts.interval);

const client = new LinuxCNCServiceClient(
  address,
  grpc.credentials.createInsecure()
);

function formatPosition(pos: { x: number; y: number; z: number }): string {
  return `X:${pos.x.toFixed(3).padStart(8)} Y:${pos.y.toFixed(3).padStart(8)} Z:${pos.z.toFixed(3).padStart(8)}`;
}

function formatState(status: LinuxCNCStatus): string {
  const mode = taskModeToJSON(status.task!.taskMode).replace("MODE_", "");
  const state = taskStateToJSON(status.task!.taskState).replace("STATE_", "");
  const interp = interpStateToJSON(status.task!.interpState).replace(
    "INTERP_",
    ""
  );
  return `${mode}/${state}/${interp}`;
}

console.log(`Streaming status from ${address} (interval: ${intervalMs}ms)`);
console.log("Press Ctrl+C to stop\n");
console.log("-".repeat(80));

const request = StreamStatusRequest.create();
request.intervalMs = intervalMs;

const stream = client.streamStatus(request);

let updateCount = 0;
const startTime = Date.now();

stream.on("data", (status: LinuxCNCStatus) => {
  updateCount++;
  const elapsed = (Date.now() - startTime) / 1000;

  const pos = formatPosition(status.position!.actualPosition!);
  const state = formatState(status);
  const vel = status.trajectory!.currentVel;
  const feed = status.trajectory!.feedrate * 100;

  // Format spindle info
  let spindleInfo = "";
  if (status.spindles.length > 0 && status.spindles[0].speed > 0) {
    spindleInfo = ` S:${status.spindles[0].speed.toFixed(0)}`;
  }

  process.stdout.write(
    `\r[${updateCount.toString().padStart(6)}] ${pos} | ${state.padEnd(20)} | V:${vel.toFixed(2).padStart(7)} F:${feed.toFixed(1).padStart(5)}%${spindleInfo}  `
  );
});

stream.on("error", (err: grpc.ServiceError) => {
  if (err.code === grpc.status.CANCELLED) {
    // Normal cancellation
  } else {
    console.error(`\ngRPC error: ${err.code}: ${err.details}`);
    process.exit(1);
  }
});

stream.on("end", () => {
  const elapsed = (Date.now() - startTime) / 1000;
  console.log(
    `\n\nReceived ${updateCount} updates in ${elapsed.toFixed(1)}s (${(updateCount / elapsed).toFixed(1)} updates/sec)`
  );
  client.close();
});

// Handle Ctrl+C
process.on("SIGINT", () => {
  stream.cancel();
});
