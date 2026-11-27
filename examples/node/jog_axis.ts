/**
 * Jog Axis Example
 *
 * Jogs the X axis by a fixed increment, then jogs the Y axis continuously for
 * half a second and stops it.
 *
 * Usage:
 *   npx tsx jog_axis.ts [--host HOST] [--port PORT] [--skip-demo]
 *
 * Before it moves anything the example checks that the machine is on, that
 * every joint is homed and that the interpreter is idle. It does not take the
 * machine out of E-stop, power it on or home it: do that at the machine first.
 * It then switches to manual mode and turns teleop on, which is what an axis
 * jog needs.
 *
 * Safety Warning:
 *   This script moves the machine! Ensure you have clear access to E-stop
 *   and understand the jog parameters before running.
 */

import { program } from "commander";
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  LinuxCNCCommand,
  LinuxCNCStatus,
  CommandResponse,
  WaitCompleteRequest,
  Metadata,
  TaskMode,
  TaskState,
  TrajMode,
  InterpState,
  JogType,
  RcsStatus,
  taskStateToJSON,
  interpStateToJSON,
  credentials,
} from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("--skip-demo", "Skip demo movements (just show status)")
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;

// Deadlines, in milliseconds. Every RPC gets one.
const RPC_TIMEOUT_MS = 5000; // GetStatus and SendCommand
const JOG_STOP_TIMEOUT_MS = 2000; // The jog stop has its own short deadline
const STATE_CHANGE_TIMEOUT_MS = 5000; // A mode or teleop change must finish within this
const WAIT_DEADLINE_MARGIN_MS = 5000; // WaitComplete's client deadline = its timeout + this
const MOTION_START_TIMEOUT_MS = 2000; // A jog that has not moved by now was refused
const MOTION_TIMEOUT_MS = 10000; // A jog must be finished and stopped within this
const POLL_INTERVAL_MS = 50;

// The demo moves. Jog velocity is in machine units per second.
const AXIS_LETTERS = "XYZABCUVW";
const INCREMENT_AXIS = 0; // X
const CONTINUOUS_AXIS = 1; // Y
const JOG_VELOCITY = 1.0; // Machine units per second
const INCREMENT_DISTANCE = 1.0; // Machine units
const CONTINUOUS_JOG_SECONDS = 0.5;
const MOVED_THRESHOLD = 0.01; // Machine units; more than feedback jitter, far less than either jog
const STOPPED_VELOCITY = 1e-6;

class InterruptedError extends Error {}

// Set by Ctrl+C or SIGTERM. The handlers only set this flag; the waits below
// check it, so the program always leaves through the code that stops the jog.
let interrupted = false;

function installInterruptHandlers(): void {
  const rememberInterrupt = (): void => {
    interrupted = true;
  };
  process.on("SIGINT", rememberInterrupt);
  process.on("SIGTERM", rememberInterrupt);
}

let outputLossIsIgnored = false;

/**
 * Keep running when the output has gone away.
 *
 * When the reader of a pipe closes it, a write to process.stdout or
 * process.stderr emits an 'error' event, and an 'error' event nobody listens
 * for ends the process: possibly between starting a jog and stopping it.
 * With these listeners the write is simply lost and the program carries on
 * to the stop. Called just before each jog starts.
 */
function keepRunningWhenOutputIsLost(): void {
  if (outputLossIsIgnored) {
    return;
  }
  outputLossIsIgnored = true;
  const ignoreLostOutput = (): void => {};
  process.stdout.on("error", ignoreLostOutput);
  process.stderr.on("error", ignoreLostOutput);
}

/** Sleep, but throw InterruptedError as soon as an interrupt arrives. */
async function pause(milliseconds: number): Promise<void> {
  const wakeTime = Date.now() + milliseconds;
  while (!interrupted && Date.now() < wakeTime) {
    await new Promise((resolve) => setTimeout(resolve, Math.min(20, wakeTime - Date.now())));
  }
  if (interrupted) {
    throw new InterruptedError("Interrupted");
  }
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

let serial = 0;

function nextSerial(): number {
  return ++serial;
}

function deadlineIn(milliseconds: number): { deadline: Date } {
  return { deadline: new Date(Date.now() + milliseconds) };
}

function getStatus(): Promise<LinuxCNCStatus> {
  return new Promise((resolve, reject) => {
    client.getStatus(GetStatusRequest.create(), new Metadata(), deadlineIn(RPC_TIMEOUT_MS), (err, status) => {
      if (err) reject(err);
      else resolve(status);
    });
  });
}

/** Send a command. RCS_DONE in the response means dispatched, not finished. */
function sendCommand(cmd: LinuxCNCCommand, timeoutMs: number = RPC_TIMEOUT_MS): Promise<CommandResponse> {
  cmd.serial = nextSerial();
  cmd.timestamp = Date.now() * 1000000;
  return new Promise((resolve, reject) => {
    client.sendCommand(cmd, new Metadata(), deadlineIn(timeoutMs), (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

/** Wait until the command that `sent` answers has finished. */
function waitComplete(sent: CommandResponse, timeoutMs: number): Promise<CommandResponse> {
  const request = WaitCompleteRequest.create();
  request.timeout = timeoutMs / 1000;
  if (sent.linuxcncSerial !== undefined) {
    // Wait on this command's serial.
    request.linuxcncSerial = sent.linuxcncSerial;
  } else {
    console.log("  Note: the server returned no linuxcnc_serial; waiting on the latest command sent by any client instead.");
  }
  // The client deadline must be longer than the wait asked of the server.
  return new Promise((resolve, reject) => {
    client.waitComplete(request, new Metadata(), deadlineIn(timeoutMs + WAIT_DEADLINE_MARGIN_MS), (err, response) => {
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

/** Turn teleop on (axis jogs) or off (joint jogs, homing). */
function setTeleop(enable: boolean): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.teleop = { enable };
  return sendCommand(cmd);
}

/** Start a continuous axis jog. velocity > 0 for positive, < 0 for negative. */
function jogContinuous(axis: number, velocity: number): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.jog = {
    type: JogType.JOG_CONTINUOUS,
    isJoint: false, // Axis jog: needs teleop on
    index: axis, // 0=X, 1=Y, 2=Z, etc.
    velocity,
    increment: 0,
  };
  return sendCommand(cmd);
}

/** Jog an axis by a fixed distance. */
function jogIncrement(axis: number, velocity: number, distance: number): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.jog = {
    type: JogType.JOG_INCREMENT,
    isJoint: false,
    index: axis,
    velocity,
    increment: distance,
  };
  return sendCommand(cmd);
}

/** Stop jogging an axis. Uses its own short deadline. */
function jogStop(axis: number): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.jog = {
    type: JogType.JOG_STOP,
    isJoint: false,
    index: axis,
    velocity: 0,
    increment: 0,
  };
  return sendCommand(cmd, JOG_STOP_TIMEOUT_MS);
}

function axisPosition(status: LinuxCNCStatus, axis: number): number {
  const position = status.position?.actualPosition;
  if (!position) {
    throw new Error("The status has no position");
  }
  return [
    position.x, position.y, position.z,
    position.a, position.b, position.c,
    position.u, position.v, position.w,
  ][axis];
}

/** The newest messages the server has read from LinuxCNC, if it reads them. */
function recentOperatorMessages(status: LinuxCNCStatus): string {
  if (status.errors.length === 0) {
    return "Look at the operator messages in the LinuxCNC GUI for the reason.";
  }
  return "Recent LinuxCNC messages: " + status.errors.slice(-3).map((error) => error.message).join("; ");
}

/** Wait for a mode or teleop command to finish, then check the status shows it. */
async function changeAndConfirm(
  sent: CommandResponse,
  description: string,
  isConfirmed: (status: LinuxCNCStatus) => boolean
): Promise<void> {
  if (sent.status !== RcsStatus.RCS_DONE) {
    throw new Error(`${description} was refused: ${sent.errorMessage}`);
  }

  const finished = await waitComplete(sent, STATE_CHANGE_TIMEOUT_MS);
  if (finished.status === RcsStatus.RCS_EXEC) {
    throw new Error(`${description} had not finished after ${STATE_CHANGE_TIMEOUT_MS / 1000} s`);
  }
  if (finished.status !== RcsStatus.RCS_DONE) {
    throw new Error(`${description} failed: ${finished.errorMessage}`);
  }

  // Finished is not the same as in effect: check the status, with a deadline.
  const deadline = Date.now() + STATE_CHANGE_TIMEOUT_MS;
  while (!isConfirmed(await getStatus())) {
    if (Date.now() >= deadline) {
      throw new Error(`${description} did not show in the status within ${STATE_CHANGE_TIMEOUT_MS / 1000} s`);
    }
    await pause(POLL_INTERVAL_MS);
  }
}

/** Check the machine is on, homed and idle, then set manual mode and teleop. */
async function prepareForAxisJog(): Promise<void> {
  let status = await getStatus();
  const task = status.task;
  const trajectory = status.trajectory;
  if (!task || !trajectory) {
    throw new Error("The status has no task or trajectory section");
  }

  // Never take the machine out of E-stop or power it on from here.
  if (task.taskState !== TaskState.STATE_ON) {
    throw new Error(
      `The machine is not on (state ${taskStateToJSON(task.taskState)}). Take it out of E-stop ` +
        "and turn it on at the machine, then run this again."
    );
  }

  // A 1.0 server lists 16 joint entries, of which only the first
  // trajectory.joints are real. Newer servers list only those.
  // An axis jog needs teleop, and teleop needs every joint homed.
  const unhomedJoints = status.joints
    .slice(0, trajectory.joints)
    .filter((joint) => !joint.homed)
    .map((joint) => joint.jointNumber);
  if (unhomedJoints.length > 0) {
    throw new Error(
      `Joints ${unhomedJoints.join(", ")} are not homed. Home the machine first; this example does not home it.`
    );
  }

  for (const axis of [INCREMENT_AXIS, CONTINUOUS_AXIS]) {
    if ((trajectory.axisMask & (1 << axis)) === 0) {
      throw new Error(`This machine has no ${AXIS_LETTERS[axis]} axis.`);
    }
  }

  // Checked whatever the mode is: never jog, or change mode, under a
  // running program or MDI command.
  if (task.interpState !== InterpState.INTERP_IDLE) {
    throw new Error(
      "The interpreter is still busy with a program or MDI command " +
        `(state ${interpStateToJSON(task.interpState)}). Nothing was moved; wait for it to finish and run this again.`
    );
  }

  if (task.taskMode !== TaskMode.MODE_MANUAL) {
    console.log("Setting manual mode...");
    await changeAndConfirm(
      await setMode(TaskMode.MODE_MANUAL),
      "Manual mode",
      (newStatus) => newStatus.task?.taskMode === TaskMode.MODE_MANUAL
    );
    status = await getStatus();
  }

  if (status.trajectory?.motionMode !== TrajMode.TRAJ_MODE_TELEOP) {
    console.log("Turning teleop on...");
    await changeAndConfirm(
      await setTeleop(true),
      "Teleop",
      (newStatus) => newStatus.trajectory?.motionMode === TrajMode.TRAJ_MODE_TELEOP
    );
  }
}

/** Send the jog stop and insist that it was dispatched. */
async function stopJog(axis: number): Promise<void> {
  const axisLetter = AXIS_LETTERS[axis];
  let sent: CommandResponse;
  try {
    sent = await jogStop(axis);
  } catch (err) {
    throw new Error(
      `The jog stop for ${axisLetter} did not get through (${err}). The axis may still be moving: use the E-stop.`
    );
  }
  if (sent.status !== RcsStatus.RCS_DONE) {
    throw new Error(
      `The jog stop for ${axisLetter} was refused: ${sent.errorMessage}. The axis may still be moving: use the E-stop.`
    );
  }
}

/**
 * Wait until the axis has moved and stopped again; return its position.
 *
 * A jog that LinuxCNC refuses still answers RCS_DONE, so the only proof of a
 * jog is the status. trajectory.inpos stays true during a jog; velocity is
 * what shows motion.
 */
async function waitForMotionToEnd(axis: number, startPosition: number): Promise<number> {
  const axisLetter = AXIS_LETTERS[axis];
  const startedWaiting = Date.now();
  for (;;) {
    const status = await getStatus();
    const position = axisPosition(status, axis);
    const hasMoved = Math.abs(position - startPosition) > MOVED_THRESHOLD;
    const isStopped = Math.abs(status.trajectory?.currentVel ?? 0) < STOPPED_VELOCITY;
    if (hasMoved && isStopped) {
      return position;
    }

    const waitedMs = Date.now() - startedWaiting;
    if (!hasMoved && waitedMs >= MOTION_START_TIMEOUT_MS) {
      throw new Error(
        `${axisLetter} did not move, so LinuxCNC refused the jog. ${recentOperatorMessages(status)}`
      );
    }
    if (waitedMs >= MOTION_TIMEOUT_MS) {
      throw new Error(`${axisLetter} was still moving after ${MOTION_TIMEOUT_MS / 1000} s`);
    }
    await pause(POLL_INTERVAL_MS);
  }
}

/** Jog one axis by a fixed distance and wait until it has arrived. */
async function demoIncrementalJog(): Promise<void> {
  const axisLetter = AXIS_LETTERS[INCREMENT_AXIS];
  console.log("\n--- Incremental Jog Demo ---");
  console.log(`Jogging ${axisLetter} by +${INCREMENT_DISTANCE} at ${JOG_VELOCITY} units/s...`);

  const startPosition = axisPosition(await getStatus(), INCREMENT_AXIS);
  await pause(0); // Do not start a jog after an interrupt
  keepRunningWhenOutputIsLost();
  let endPosition: number;
  try {
    // Nothing is printed between here and the stop.
    const sent = await jogIncrement(INCREMENT_AXIS, JOG_VELOCITY, INCREMENT_DISTANCE);
    if (sent.status !== RcsStatus.RCS_DONE) {
      throw new Error(`Incremental jog was refused: ${sent.errorMessage}`);
    }
    endPosition = await waitForMotionToEnd(INCREMENT_AXIS, startPosition);
  } catch (err) {
    // Interrupted or failed part way (including a start whose answer never
    // came): do not leave the axis moving. When the wait succeeds no stop is
    // needed: the status showed the axis moved and is standing still.
    await stopJog(INCREMENT_AXIS);
    throw err;
  }

  console.log(`${axisLetter} moved from ${startPosition.toFixed(4)} to ${endPosition.toFixed(4)}`);
}

/** Jog one axis continuously for a moment, then stop it. */
async function demoContinuousJog(): Promise<void> {
  const axisLetter = AXIS_LETTERS[CONTINUOUS_AXIS];
  console.log("\n--- Continuous Jog Demo ---");
  console.log(`Jogging ${axisLetter} at +${JOG_VELOCITY} units/s for ${CONTINUOUS_JOG_SECONDS} seconds...`);

  const startPosition = axisPosition(await getStatus(), CONTINUOUS_AXIS);
  await pause(0); // Do not start a jog after an interrupt
  keepRunningWhenOutputIsLost();
  try {
    // From here on the stop below is always sent: after the sleep, on
    // Ctrl+C and on any error, including a start whose answer never came.
    // Nothing is printed until the stop has been sent.
    const sent = await jogContinuous(CONTINUOUS_AXIS, JOG_VELOCITY);
    if (sent.status !== RcsStatus.RCS_DONE) {
      throw new Error(`Continuous jog was refused: ${sent.errorMessage}`);
    }
    await pause(CONTINUOUS_JOG_SECONDS * 1000);
  } finally {
    await stopJog(CONTINUOUS_AXIS);
  }
  console.log("Jog stop sent.");

  const endPosition = await waitForMotionToEnd(CONTINUOUS_AXIS, startPosition);
  console.log(`${axisLetter} moved from ${startPosition.toFixed(4)} to ${endPosition.toFixed(4)}`);
}

async function main(): Promise<void> {
  // Installed before anything moves.
  installInterruptHandlers();

  // No process.exit() in here: it would skip the cleanup in the finally blocks.
  try {
    // Show initial status
    const status = await getStatus();
    const position = status.position?.actualPosition;
    if (!position) {
      throw new Error("No position data available");
    }
    console.log(`Current position: X=${position.x.toFixed(4)} Y=${position.y.toFixed(4)} Z=${position.z.toFixed(4)}`);

    if (opts.skipDemo) {
      console.log("Skipping demo movements (--skip-demo)");
      return;
    }

    await prepareForAxisJog();
    await demoIncrementalJog();
    await demoContinuousJog();

    console.log("\nJog demo complete!");
  } catch (err) {
    console.error(err instanceof Error ? `Error: ${err.message}` : `Error: ${err}`);
    process.exitCode = err instanceof InterruptedError ? 130 : 1;
  } finally {
    client.close();
  }
}

main();
