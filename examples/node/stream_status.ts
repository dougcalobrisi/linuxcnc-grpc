/**
 * Stream Status Example
 *
 * Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
 * This is useful for building dashboards or monitoring applications.
 *
 * Usage:
 *   npx tsx stream_status.ts [--interval 100]
 *
 * --interval is the time between updates in milliseconds, from 1 to 60000.
 *
 * Press Ctrl+C to stop streaming. The example gives up, and exits non-zero,
 * when no update has arrived for 30 seconds or for twice the interval plus 5
 * seconds, whichever is longer. It also exits non-zero when the stream fails
 * or the server cancels it.
 */

import { program } from "commander";
import {
  LinuxCNCServiceClient,
  StreamStatusRequest,
  LinuxCNCStatus,
  credentials,
  status,
  taskModeToJSON,
  taskStateToJSON,
  interpStateToJSON,
} from "linuxcnc-grpc";
import type { ServiceError } from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option(
    "--interval <interval>",
    "Update interval in milliseconds, 1 to 60000. The example gives up when no update arrives " +
      "for 30 seconds or twice the interval plus 5 seconds, whichever is longer",
    "100"
  )
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;
// Number(), not parseInt(): "100abc" must not pass as 100.
const intervalMs = opts.interval.trim() === "" ? NaN : Number(opts.interval);

// The server accepts intervals up to 60 seconds.
const MIN_INTERVAL_MS = 1;
const MAX_INTERVAL_MS = 60000;

// A stream has no overall deadline: it runs until Ctrl+C. Instead, give up
// when the server has sent nothing for too long: the larger of
// MIN_STREAM_IDLE_TIMEOUT_MS and STREAM_IDLE_INTERVALS update intervals plus
// STREAM_IDLE_MARGIN_MS.
const MIN_STREAM_IDLE_TIMEOUT_MS = 30000;
const STREAM_IDLE_INTERVALS = 2;
const STREAM_IDLE_MARGIN_MS = 5000;

/** Milliseconds without an update after which the stream counts as dead. */
function streamIdleTimeoutMs(updateIntervalMs: number): number {
  return Math.max(
    MIN_STREAM_IDLE_TIMEOUT_MS,
    STREAM_IDLE_INTERVALS * updateIntervalMs + STREAM_IDLE_MARGIN_MS
  );
}

if (!Number.isInteger(intervalMs) || intervalMs < MIN_INTERVAL_MS || intervalMs > MAX_INTERVAL_MS) {
  console.error("Error: --interval must be a whole number of milliseconds from 1 to 60000");
  process.exit(1);
}

console.error(`Connecting to ${address}...`);
const client = new LinuxCNCServiceClient(
  address,
  credentials.createInsecure(),
  {
    'grpc.initial_reconnect_backoff_ms': 1000,
    'grpc.max_reconnect_backoff_ms': 5000,
  }
);

function formatPosition(pos: { x: number; y: number; z: number }): string {
  return `X:${pos.x.toFixed(3).padStart(8)} Y:${pos.y.toFixed(3).padStart(8)} Z:${pos.z.toFixed(3).padStart(8)}`;
}

function formatState(status: LinuxCNCStatus): string {
  const task = status.task;
  if (!task) {
    return "NO_TASK";
  }
  const mode = taskModeToJSON(task.taskMode).replace("MODE_", "");
  const state = taskStateToJSON(task.taskState).replace("STATE_", "");
  const interp = interpStateToJSON(task.interpState).replace(
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

const idleTimeoutMs = streamIdleTimeoutMs(intervalMs);

const stream = client.streamStatus(request);

let updateCount = 0;
const startTime = Date.now();

// True once this program has cancelled the stream itself: on Ctrl+C or from
// the idle timer. Set before cancel() is called.
let cancelledByThisProgram = false;

let streamWentSilent = false;
const idleTimer = setTimeout(() => {
  streamWentSilent = true;
  cancelledByThisProgram = true;
  stream.cancel();
}, idleTimeoutMs);

// Runs once, when the stream is over for whatever reason.
let hasFinished = false;
function finish(): void {
  if (hasFinished) return;
  hasFinished = true;
  clearTimeout(idleTimer);
  client.close();
  const elapsed = (Date.now() - startTime) / 1000;
  console.log(
    `\n\nReceived ${updateCount} updates in ${elapsed.toFixed(1)}s (${(updateCount / elapsed).toFixed(1)} updates/sec)`
  );
  if (streamWentSilent) {
    console.error(`No status update for ${idleTimeoutMs / 1000} seconds; giving up.`);
    process.exitCode = 1;
  }
}

stream.on("data", (status: LinuxCNCStatus) => {
  updateCount++;
  idleTimer.refresh();

  const position = status.position;
  if (!position) return;
  const actualPosition = position.actualPosition;
  if (!actualPosition) return;
  const trajectory = status.trajectory;
  if (!trajectory) return;

  const pos = formatPosition(actualPosition);
  const state = formatState(status);
  const vel = trajectory.currentVel;
  const feed = trajectory.feedrate * 100;

  // Format spindle info
  let spindleInfo = "";
  if (status.spindles.length > 0 && status.spindles[0].speed > 0) {
    spindleInfo = ` S:${status.spindles[0].speed.toFixed(0)}`;
  }

  process.stdout.write(
    `\r[${updateCount.toString().padStart(6)}] ${pos} | ${state.padEnd(20)} | V:${vel.toFixed(2).padStart(7)} F:${feed.toFixed(1).padStart(5)}%${spindleInfo}  `
  );
});

stream.on("error", (err: ServiceError) => {
  // CANCELLED is expected only when this program cancelled the stream: on
  // Ctrl+C, or from the idle timer (reported in finish()). A stream the
  // server cancelled is a failure like any other error.
  if (!(err.code === status.CANCELLED && cancelledByThisProgram)) {
    console.error(`\ngRPC error: ${err.code}: ${err.details}`);
    process.exitCode = 1;
  }
  finish();
});

stream.on("end", finish);

// Handle Ctrl+C
process.on("SIGINT", () => {
  cancelledByThisProgram = true;
  stream.cancel();
});
