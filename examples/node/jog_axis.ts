/**
 * Jog Axis Example
 *
 * Demonstrates jogging an axis using the LinuxCNC gRPC server.
 * Supports both continuous jogging and incremental jogging.
 *
 * Usage:
 *   npx tsx jog_axis.ts [--host HOST] [--port PORT] [--skip-demo]
 *
 * Safety Warning:
 *   This script moves the machine! Ensure you have clear access to E-stop
 *   and understand the jog parameters before running.
 */

import * as grpc from "@grpc/grpc-js";
import { program } from "commander";
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  LinuxCNCCommand,
  LinuxCNCStatus,
  CommandResponse,
  TaskMode,
  TaskState,
  JogType,
  RcsStatus,
} from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("--skip-demo", "Skip demo movements (just show status)")
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;

const client = new LinuxCNCServiceClient(
  address,
  grpc.credentials.createInsecure()
);

let serial = 0;

function nextSerial(): number {
  return ++serial;
}

function getStatus(): Promise<LinuxCNCStatus> {
  return new Promise((resolve, reject) => {
    client.getStatus(GetStatusRequest.create(), (err, status) => {
      if (err) reject(err);
      else resolve(status);
    });
  });
}

function sendCommand(cmd: LinuxCNCCommand): Promise<CommandResponse> {
  cmd.serial = nextSerial();
  cmd.timestamp = Date.now() * 1000000;
  return new Promise((resolve, reject) => {
    client.sendCommand(cmd, (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

function setMode(mode: TaskMode): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.mode = { mode };
  return sendCommand(cmd);
}

function setState(state: TaskState): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.state = { state };
  return sendCommand(cmd);
}

function jogContinuous(axis: number, velocity: number): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.jog = {
    type: JogType.JOG_CONTINUOUS,
    isJoint: false,
    index: axis,
    velocity,
    increment: 0,
  };
  return sendCommand(cmd);
}

function jogIncrement(
  axis: number,
  velocity: number,
  increment: number
): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.jog = {
    type: JogType.JOG_INCREMENT,
    isJoint: false,
    index: axis,
    velocity: Math.abs(velocity),
    increment,
  };
  return sendCommand(cmd);
}

function jogStop(axis: number): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.jog = {
    type: JogType.JOG_STOP,
    isJoint: false,
    index: axis,
    velocity: 0,
    increment: 0,
  };
  return sendCommand(cmd);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function ensureMachineReady(): Promise<boolean> {
  let status = await getStatus();

  // Check E-stop
  if (status.task!.taskState === TaskState.STATE_ESTOP) {
    console.log("Machine is in E-stop. Resetting...");
    const resp = await setState(TaskState.STATE_ESTOP_RESET);
    if (resp.status !== RcsStatus.RCS_DONE) {
      console.log(`Failed to reset E-stop: ${resp.errorMessage}`);
      return false;
    }
    await sleep(100);
  }

  // Power on
  status = await getStatus();
  if (status.task!.taskState !== TaskState.STATE_ON) {
    console.log("Powering on machine...");
    const resp = await setState(TaskState.STATE_ON);
    if (resp.status !== RcsStatus.RCS_DONE) {
      console.log(`Failed to power on: ${resp.errorMessage}`);
      return false;
    }
    await sleep(100);
  }

  // Set manual mode for jogging
  status = await getStatus();
  if (status.task!.taskMode !== TaskMode.MODE_MANUAL) {
    console.log("Setting manual mode...");
    const resp = await setMode(TaskMode.MODE_MANUAL);
    if (resp.status !== RcsStatus.RCS_DONE) {
      console.log(`Failed to set manual mode: ${resp.errorMessage}`);
      return false;
    }
    await sleep(100);
  }

  return true;
}

async function demoIncrementalJog(): Promise<void> {
  console.log("\n--- Incremental Jog Demo ---");
  console.log("Jogging X axis +1.0 units...");

  // Jog X axis positive by 1.0 unit at 100 units/min
  const resp = await jogIncrement(0, 100.0, 1.0);
  if (resp.status !== RcsStatus.RCS_DONE) {
    console.log(`Jog failed: ${resp.errorMessage}`);
    return;
  }

  // Wait for motion to complete
  await sleep(1000);

  // Show new position
  const status = await getStatus();
  const pos = status.position!.actualPosition!;
  console.log(`New position: X=${pos.x.toFixed(4)} Y=${pos.y.toFixed(4)} Z=${pos.z.toFixed(4)}`);
}

async function demoContinuousJog(): Promise<void> {
  console.log("\n--- Continuous Jog Demo ---");
  console.log("Jogging Y axis positive for 0.5 seconds...");

  // Start continuous jog on Y axis at 50 units/min
  let resp = await jogContinuous(1, 50.0);
  if (resp.status !== RcsStatus.RCS_DONE) {
    console.log(`Jog start failed: ${resp.errorMessage}`);
    return;
  }

  // Let it jog for a bit
  await sleep(500);

  // Stop the jog
  console.log("Stopping jog...");
  resp = await jogStop(1);
  if (resp.status !== RcsStatus.RCS_DONE) {
    console.log(`Jog stop failed: ${resp.errorMessage}`);
    return;
  }

  // Show new position
  const status = await getStatus();
  const pos = status.position!.actualPosition!;
  console.log(`New position: X=${pos.x.toFixed(4)} Y=${pos.y.toFixed(4)} Z=${pos.z.toFixed(4)}`);
}

async function main(): Promise<void> {
  try {
    // Show initial status
    const status = await getStatus();
    const pos = status.position!.actualPosition!;
    console.log(`Current position: X=${pos.x.toFixed(4)} Y=${pos.y.toFixed(4)} Z=${pos.z.toFixed(4)}`);

    if (opts.skipDemo) {
      console.log("Skipping demo movements (--skip-demo)");
      return;
    }

    // Ensure machine is ready for jogging
    if (!(await ensureMachineReady())) {
      console.log("Could not prepare machine for jogging");
      process.exit(1);
    }

    // Run demos
    await demoIncrementalJog();
    await demoContinuousJog();

    console.log("\nJog demo complete!");
  } catch (err) {
    console.error(`Error: ${err}`);
    process.exit(1);
  } finally {
    client.close();
  }
}

main();
