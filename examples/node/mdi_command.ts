/**
 * MDI Command Example
 *
 * Execute G-code commands via MDI (Manual Data Input) mode.
 * This is useful for sending individual G-code commands without loading a file.
 *
 * Usage:
 *   npx tsx mdi_command.ts "G0 X10 Y10"
 *   npx tsx mdi_command.ts --interactive
 *
 * Before it sends anything the example checks that the machine is on, that
 * every joint is homed and that the interpreter is idle. It does not take the
 * machine out of E-stop, power it on or home it: do that at the machine first.
 * It then switches to MDI mode. The checks are repeated before every command,
 * so in interactive mode a command is not sent while an earlier one is still
 * running.
 *
 * Safety Warning:
 *   MDI commands execute immediately on the machine. Understand what
 *   each command does before running it. Ctrl+C stops this program, not the
 *   machine: a command that was already sent keeps running.
 */

import * as readline from "readline";
import { program } from "commander";
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  LinuxCNCCommand,
  WaitCompleteRequest,
  LinuxCNCStatus,
  CommandResponse,
  Metadata,
  TaskMode,
  TaskState,
  InterpState,
  RcsStatus,
  taskStateToJSON,
  interpStateToJSON,
  credentials,
} from "linuxcnc-grpc";

program
  .argument("[command]", "G-code command to execute")
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("-i, --interactive", "Enter interactive MDI mode")
  .option("--no-wait", "Don't wait for command completion")
  .option("--wait-timeout <seconds>", "Seconds to wait for a command to finish; more than 0 and at most 86400", "60")
  .parse();

const opts = program.opts();
const args = program.args;
const command = args[0];
const address = `${opts.host}:${opts.port}`;
// Number(), not parseFloat(): "60abc" must not pass as 60.
const waitTimeoutSeconds = opts.waitTimeout.trim() === "" ? NaN : Number(opts.waitTimeout);
const waitTimeoutMs = waitTimeoutSeconds * 1000;

// Deadlines, in milliseconds. Every RPC gets one.
const RPC_TIMEOUT_MS = 5000; // GetStatus and SendCommand
const STATE_CHANGE_TIMEOUT_MS = 5000; // A mode change must finish within this
const WAIT_DEADLINE_MARGIN_MS = 5000; // WaitComplete's client deadline = its timeout + this
const POLL_INTERVAL_MS = 50;
const STOPPED_VELOCITY = 1e-6; // Below this the machine is standing still
const MAX_MDI_TIMEOUT_SECONDS = 86400; // Longest --wait-timeout accepted (one day)

if (!command && !opts.interactive) {
  console.log("Usage: npx tsx mdi_command.ts [options] \"G-code command\"");
  console.log("       npx tsx mdi_command.ts --interactive");
  console.log("\nExamples:");
  console.log('  npx tsx mdi_command.ts "G0 X10 Y10"');
  console.log('  npx tsx mdi_command.ts "G1 X20 F100"');
  console.log("  npx tsx mdi_command.ts --interactive");
  process.exit(1);
}
// Checked before any RPC: 0 would make the server use its own default, and a
// negative or huge value would give a client deadline that has already
// expired or cannot be represented, after the command was dispatched.
if (
  !Number.isFinite(waitTimeoutSeconds) ||
  !(waitTimeoutSeconds > 0 && waitTimeoutSeconds <= MAX_MDI_TIMEOUT_SECONDS)
) {
  console.error("Error: --wait-timeout must be a number of seconds greater than 0 and at most 86400");
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
function sendCommand(cmd: LinuxCNCCommand): Promise<CommandResponse> {
  cmd.serial = nextSerial();
  cmd.timestamp = Date.now() * 1000000;
  return new Promise((resolve, reject) => {
    client.sendCommand(cmd, new Metadata(), deadlineIn(RPC_TIMEOUT_MS), (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

/**
 * Wait until the command that `sent` answers has finished.
 *
 * The result is RCS_DONE, RCS_ERROR (failed) or RCS_EXEC (the timeout
 * expired and the command is still running). RCS_DONE means finished only if
 * LinuxCNC took no later command meanwhile; see confirmMDIFinished.
 */
function waitComplete(sent: CommandResponse, timeoutMs: number): Promise<CommandResponse> {
  const request = WaitCompleteRequest.create();
  request.timeout = timeoutMs / 1000;
  if (sent.linuxcncSerial !== undefined) {
    // Wait on this command's serial. WaitCompleteRequest.serial would not
    // do that: the server ignores it.
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

/** Send an MDI command. */
function mdi(gcode: string): Promise<CommandResponse> {
  const cmd = LinuxCNCCommand.create();
  cmd.mdi = { command: gcode };
  return sendCommand(cmd);
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function machineIsOn(status: LinuxCNCStatus): boolean {
  return status.task?.taskState === TaskState.STATE_ON;
}

/**
 * True while a program, an MDI command or a jog is under way.
 *
 * The interpreter state alone does not tell. On the LinuxCNC 2.9 simulator
 * it read INTERP_IDLE for the whole of some MDI moves, while
 * trajectory.inpos was false in every reading taken during a move. A jog
 * leaves inpos true, so velocity is looked at as well.
 */
function machineIsBusy(status: LinuxCNCStatus): boolean {
  return (
    status.task?.interpState !== InterpState.INTERP_IDLE ||
    status.trajectory?.inpos !== true ||
    Math.abs(status.trajectory.currentVel) >= STOPPED_VELOCITY
  );
}

/** Check the machine is on, homed and idle, then make sure it is in MDI mode. */
async function ensureMDIReady(): Promise<void> {
  const status = await getStatus();
  const task = status.task;
  const trajectory = status.trajectory;
  if (!task || !trajectory) {
    throw new Error("The status has no task or trajectory section");
  }

  // Never take the machine out of E-stop or power it on from here.
  if (!machineIsOn(status)) {
    throw new Error(
      `The machine is not on (state ${taskStateToJSON(task.taskState)}). Take it out of E-stop ` +
        "and turn it on at the machine, then run this again."
    );
  }

  // A 1.0 server lists 16 joint entries, of which only the first
  // trajectory.joints are real. Newer servers list only those.
  const unhomedJoints = status.joints
    .slice(0, trajectory.joints)
    .filter((joint) => !joint.homed)
    .map((joint) => joint.jointNumber);
  if (unhomedJoints.length > 0) {
    throw new Error(
      `Joints ${unhomedJoints.join(", ")} are not homed. Home the machine first; this example does not home it.`
    );
  }

  // Checked every time, also when the machine is already in MDI mode: a
  // command whose wait timed out is still running, and so may a program be.
  if (machineIsBusy(status)) {
    throw new Error(
      "The machine is still busy with a previous command, a running program or a jog " +
        `(interpreter ${interpStateToJSON(task.interpState)}, in position: ${trajectory.inpos}, ` +
        `velocity ${trajectory.currentVel}). Nothing was sent; wait for it to finish and try again.`
    );
  }

  if (task.taskMode === TaskMode.MODE_MDI) {
    return;
  }

  console.log("Setting MDI mode...");
  const sent = await setMode(TaskMode.MODE_MDI);
  if (sent.status !== RcsStatus.RCS_DONE) {
    throw new Error(`MDI mode was refused: ${sent.errorMessage}`);
  }

  const finished = await waitComplete(sent, STATE_CHANGE_TIMEOUT_MS);
  if (finished.status === RcsStatus.RCS_EXEC) {
    throw new Error(`MDI mode had not finished after ${STATE_CHANGE_TIMEOUT_MS / 1000} s`);
  }
  if (finished.status !== RcsStatus.RCS_DONE) {
    throw new Error(`MDI mode failed: ${finished.errorMessage}`);
  }

  // Finished is not the same as in effect: check the status, with a deadline.
  const deadline = Date.now() + STATE_CHANGE_TIMEOUT_MS;
  while ((await getStatus()).task?.taskMode !== TaskMode.MODE_MDI) {
    if (Date.now() >= deadline) {
      throw new Error(`MDI mode did not show in the status within ${STATE_CHANGE_TIMEOUT_MS / 1000} s`);
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

/**
 * Poll the status until it shows that the MDI command is no longer running.
 *
 * RCS_DONE from WaitComplete is not enough. The server also answers it as
 * soon as LinuxCNC has taken a later command from any client (the GUI
 * changing the feed override, say), and the MDI move may then still be
 * running. The status settles it: machineIsBusy is false again.
 *
 * The machine must also still be on. An E-stop from another client ends the
 * wait with RCS_DONE too, and the status then reads idle, in position and
 * standing still although the move was cut short.
 *
 * One case cannot be told from completion here: an abort from another
 * client. It stops the move and leaves the machine on, idle and in
 * position, exactly as a finished command does. Only the position shows
 * it: the move ends short of its target.
 *
 * Resolves to false if the status did not show that by `deadlineMs` (a
 * Date.now() value). The status is read at least once, and each read has its
 * own deadline. Throws if the machine is not on.
 */
async function confirmMDIFinished(deadlineMs: number): Promise<boolean> {
  for (;;) {
    const status = await getStatus();
    if (!machineIsOn(status)) {
      const taskState = status.task?.taskState;
      throw new Error(
        `The machine is no longer on (state ${taskState === undefined ? "unknown" : taskStateToJSON(taskState)}). ` +
          "The command was stopped and did not finish."
      );
    }
    if (!machineIsBusy(status)) {
      return true;
    }
    if (Date.now() >= deadlineMs) {
      return false;
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

/** Send an MDI command and, if asked, wait until it has finished. */
async function executeMDI(gcode: string, wait: boolean): Promise<void> {
  console.log(`Executing: ${gcode}`);

  const sent = await mdi(gcode);
  if (sent.status !== RcsStatus.RCS_DONE) {
    throw new Error(`The command was refused: ${sent.errorMessage}`);
  }

  if (!wait) {
    console.log("  Dispatched (not waiting for it to finish).");
    return;
  }

  console.log("  Waiting for completion...");
  // One timeout covers both the wait and the check of the status after it.
  const waitDeadlineMs = Date.now() + waitTimeoutMs;
  const finished = await waitComplete(sent, waitTimeoutMs);
  if (finished.status === RcsStatus.RCS_EXEC) {
    throw new Error(
      `Timed out: the command was still running after ${waitTimeoutMs / 1000} s. It has not been cancelled.`
    );
  }
  if (finished.status !== RcsStatus.RCS_DONE) {
    throw new Error(`The command failed: ${finished.errorMessage}`);
  }

  if (!(await confirmMDIFinished(waitDeadlineMs))) {
    throw new Error(
      "Not confirmed: the wait ended, but the status did not show the interpreter idle and the motion " +
        `in position within ${waitTimeoutMs / 1000} s. The command may still be running (a wait ends early ` +
        "when LinuxCNC takes a later command from any client). It has not been cancelled."
    );
  }
  console.log("  Done.");
}

async function printPosition(): Promise<void> {
  const position = (await getStatus()).position?.actualPosition;
  if (!position) {
    throw new Error("No position data available");
  }
  console.log(`Position: X=${position.x.toFixed(4)} Y=${position.y.toFixed(4)} Z=${position.z.toFixed(4)}`);
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Ctrl+C: close the client and leave at once, also in the middle of a wait.
 *
 * Without this the program could go on waiting for the whole --wait-timeout:
 * in a terminal the line reader takes Ctrl+C and only closes itself, which
 * does not end a call that is already being awaited.
 *
 * No abort is sent. Whether to abort what the machine is doing is the
 * operator's decision, not this example's: a command that was already sent
 * keeps running.
 */
function exitOnInterrupt(): void {
  console.error("\nInterrupted. A command that was already sent keeps running on the machine.");
  client.close();
  process.exit(130);
}

/**
 * Interactive MDI shell. Returns how many commands failed: a command that
 * fails does not end the session, but it is counted, so the caller can exit
 * non-zero. A failure to read the input is thrown.
 */
async function interactiveMode(): Promise<number> {
  let failedCommandCount = 0;
  console.log("\nInteractive MDI Mode");
  console.log("Type G-code commands to execute. Type 'quit' or 'exit' to quit.");
  console.log("Type 'status' to show current position.\n");

  const lineReader = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: "MDI> ",
  });

  // In a terminal Ctrl+C arrives here, not as a signal to the process. It is
  // not the end of the input: leave at once.
  lineReader.on("SIGINT", exitOnInterrupt);

  // Input can end (Ctrl+D, or a piped script running out) while a command is
  // still being waited for; prompting a closed reader throws.
  let inputHasEnded = false;
  lineReader.on("close", () => {
    inputHasEnded = true;
  });

  // A read error is not the end of the input: remember it, and fail below.
  let inputError: Error | undefined;
  process.stdin.on("error", (err: Error) => {
    inputError = inputError ?? err;
    lineReader.close();
  });

  lineReader.prompt();
  try {
    for await (const input of lineReader) {
      const line = input.trim();
      const lower = line.toLowerCase();

      if (lower === "quit" || lower === "exit" || lower === "q") {
        break;
      }

      if (lower === "help") {
        console.log("Commands:");
        console.log("  <G-code>  - Execute G-code command");
        console.log("  status    - Show current position");
        console.log("  quit      - Exit interactive mode");
      } else if (line) {
        try {
          if (lower === "status") {
            await printPosition();
          } else {
            // The machine may have been turned off or switched to another
            // mode since the last command, or the last command may still be
            // running.
            await ensureMDIReady();
            await executeMDI(line, true);
          }
        } catch (err) {
          failedCommandCount += 1;
          console.log(`  Error: ${errorText(err)}`);
        }
      }

      if (!inputHasEnded) {
        lineReader.prompt();
      }
    }
  } catch (err) {
    // Some Node.js versions pass the read error on through the line reader.
    inputError = inputError ?? (err instanceof Error ? err : new Error(String(err)));
  }

  lineReader.close();
  if (inputError) {
    console.log();
    throw new Error(`Reading input failed: ${inputError.message}`);
  }
  console.log("\nExiting...");
  return failedCommandCount;
}

async function main(): Promise<void> {
  // Ctrl+C when the input is not a terminal, or outside interactive mode.
  process.on("SIGINT", exitOnInterrupt);

  try {
    await ensureMDIReady();

    if (opts.interactive) {
      const failedCommandCount = await interactiveMode();
      if (failedCommandCount > 0) {
        throw new Error(`${failedCommandCount} command(s) failed in this session.`);
      }
    } else {
      await executeMDI(command, opts.wait !== false);
      await printPosition();
    }
  } catch (err) {
    console.error(`Error: ${errorText(err)}`);
    process.exitCode = 1;
  } finally {
    client.close();
  }
}

main();
