/**
 * Get LinuxCNC Status Example
 *
 * Connects to the gRPC server and displays the current machine status.
 * This is the simplest example - a good starting point for understanding the API.
 *
 * Usage:
 *   npx tsx get_status.ts [--host HOST] [--port PORT]
 */

import { program } from "commander";
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  LinuxCNCStatus,
  Metadata,
  taskModeToJSON,
  taskStateToJSON,
  execStateToJSON,
  interpStateToJSON,
  coolantStateToJSON,
  credentials,
} from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;

console.error(`Connecting to ${address}...`);
const client = new LinuxCNCServiceClient(
  address,
  credentials.createInsecure(),
  {
    'grpc.initial_reconnect_backoff_ms': 1000,
    'grpc.max_reconnect_backoff_ms': 5000,
  }
);

function printStatus(status: LinuxCNCStatus): void {
  console.log("=".repeat(60));
  console.log("LinuxCNC Status");
  console.log("=".repeat(60));

  // Task status
  const task = status.task;
  if (!task) {
    console.error("No task status available");
    return;
  }
  console.log("\n[Task]");
  console.log(`  Mode:       ${taskModeToJSON(task.taskMode)}`);
  console.log(`  State:      ${taskStateToJSON(task.taskState)}`);
  console.log(`  Exec State: ${execStateToJSON(task.execState)}`);
  console.log(`  Interp:     ${interpStateToJSON(task.interpState)}`);
  if (task.file) {
    console.log(`  File:       ${task.file}`);
  }

  // Position
  const position = status.position;
  if (!position) {
    console.error("No position status available");
    return;
  }
  const actualPosition = position.actualPosition;
  if (!actualPosition) {
    console.error("No actual position available");
    return;
  }
  const pos = actualPosition;
  console.log("\n[Position]");
  console.log(
    `  X: ${pos.x.toFixed(4).padStart(10)}  Y: ${pos.y.toFixed(4).padStart(10)}  Z: ${pos.z.toFixed(4).padStart(10)}`
  );
  if (pos.a || pos.b || pos.c) {
    console.log(
      `  A: ${pos.a.toFixed(4).padStart(10)}  B: ${pos.b.toFixed(4).padStart(10)}  C: ${pos.c.toFixed(4).padStart(10)}`
    );
  }

  // Trajectory
  const trajectory = status.trajectory;
  if (!trajectory) {
    console.error("No trajectory status available");
    return;
  }
  console.log("\n[Trajectory]");
  console.log(`  Enabled:    ${trajectory.enabled}`);
  console.log(`  Feed Rate:  ${(trajectory.feedrate * 100).toFixed(1)}%`);
  console.log(`  Rapid Rate: ${(trajectory.rapidrate * 100).toFixed(1)}%`);
  console.log(`  Velocity:   ${trajectory.currentVel.toFixed(2)}`);

  // Joints
  console.log("\n[Joints]");
  for (const joint of status.joints) {
    const homed = joint.homed ? "H" : "-";
    const enabled = joint.enabled ? "E" : "-";
    const fault = joint.fault ? "F" : "-";
    console.log(
      `  Joint ${joint.jointNumber}: [${homed}${enabled}${fault}] pos=${joint.input.toFixed(4).padStart(10)}`
    );
  }

  // Spindles
  if (status.spindles.length > 0) {
    console.log("\n[Spindles]");
    for (const spindle of status.spindles) {
      const direction =
        spindle.direction === -1
          ? "REV"
          : spindle.direction === 1
            ? "FWD"
            : "OFF";
      console.log(
        `  Spindle ${spindle.spindleNumber}: ${direction} @ ${spindle.speed.toFixed(0)} RPM`
      );
    }
  }

  // I/O
  const io = status.io;
  if (!io) {
    console.error("No I/O status available");
    return;
  }
  console.log("\n[I/O]");
  console.log(`  E-stop: ${io.estop ? "ACTIVE" : "OK"}`);
  console.log(`  Mist:   ${coolantStateToJSON(io.mist)}`);
  console.log(`  Flood:  ${coolantStateToJSON(io.flood)}`);

  // Active G-codes
  const gcode = status.gcode;
  if (!gcode) {
    console.error("No G-code status available");
    return;
  }
  if (gcode.activeGcodes.length > 0) {
    const gcodes = gcode.activeGcodes
      .filter((g) => g > 0)
      .map((g) => (g % 10 === 0 ? `G${g / 10}` : `G${(g / 10).toFixed(1)}`));
    console.log("\n[Active G-codes]");
    if (gcodes.length > 10) {
      console.log(`  ${gcodes.slice(0, 10).join(" ")}`);
      console.log(`  ${gcodes.slice(10).join(" ")}`);
    } else {
      console.log(`  ${gcodes.join(" ")}`);
    }
  }

  // Errors
  if (status.errors.length > 0) {
    console.log("\n[Errors]");
    for (const err of status.errors) {
      console.log(`  ${err.type}: ${err.message}`);
    }
  }

  console.log();
}

const deadline = new Date(Date.now() + 5000);
client.getStatus(GetStatusRequest.create(), new Metadata(), { deadline }, (err, status) => {
  if (err) {
    console.error(`gRPC error: ${err.code}: ${err.details}`);
    client.close();
    process.exit(1);
  }
  printStatus(status);
  client.close();
});
