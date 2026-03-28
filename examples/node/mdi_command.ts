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
 * Safety Warning:
 *   MDI commands execute immediately on the machine. Understand what
 *   each command does before running it.
 */

import * as readline from "readline";
import { Metadata } from "@grpc/grpc-js";
import { program } from "commander";
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  LinuxCNCCommand,
  WaitCompleteRequest,
  LinuxCNCStatus,
  CommandResponse,
  TaskMode,
  TaskState,
  RcsStatus,
  credentials,
} from "linuxcnc-grpc";

program
  .argument("[command]", "G-code command to execute")
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("-i, --interactive", "Enter interactive MDI mode")
  .option("--no-wait", "Don't wait for command completion")
  .parse();

const opts = program.opts();
const args = program.args;
const command = args[0];
const address = `${opts.host}:${opts.port}`;

if (!command && !opts.interactive) {
  console.log("Usage: npx tsx mdi_command.ts [options] \"G-code command\"");
  console.log("       npx tsx mdi_command.ts --interactive");
  console.log("\nExamples:");
  console.log('  npx tsx mdi_command.ts "G0 X10 Y10"');
  console.log('  npx tsx mdi_command.ts "G1 X20 F100"');
  console.log("  npx tsx mdi_command.ts --interactive");
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

const RPC_DEADLINE = 5000;

let serial = 0;

function nextSerial(): number {
  return ++serial;
}

function getStatus(): Promise<LinuxCNCStatus> {
  return new Promise((resolve, reject) => {
    client.getStatus(GetStatusRequest.create(), new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, status) => {
      if (err) reject(err);
      else resolve(status);
    });
  });
}

function sendCommand(cmd: LinuxCNCCommand): Promise<CommandResponse> {
  cmd.serial = nextSerial();
  cmd.timestamp = Date.now() * 1000000;
  return new Promise((resolve, reject) => {
    client.sendCommand(cmd, new Metadata(), { deadline: new Date(Date.now() + RPC_DEADLINE) }, (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

function waitComplete(cmdSerial: number, timeout: number): Promise<CommandResponse> {
  const request = WaitCompleteRequest.create();
  request.serial = cmdSerial;
  request.timeout = timeout;
  const waitDeadline = new Date(Date.now() + (timeout * 1000) + 5000);
  return new Promise((resolve, reject) => {
    client.waitComplete(request, new Metadata(), { deadline: waitDeadline }, (err, response) => {
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

async function mdi(gcode: string): Promise<{ response: CommandResponse; serial: number }> {
  const cmd = LinuxCNCCommand.create();
  cmd.mdi = { command: gcode };
  const response = await sendCommand(cmd);
  return { response, serial: cmd.serial };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function ensureMDIReady(): Promise<boolean> {
  let status = await getStatus();

  // Check E-stop
  let task = status.task;
  if (!task) {
    console.log("No task status available");
    return false;
  }
  if (task.taskState === TaskState.STATE_ESTOP) {
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
  task = status.task;
  if (!task) {
    console.log("No task status available");
    return false;
  }
  if (task.taskState !== TaskState.STATE_ON) {
    console.log("Powering on machine...");
    const resp = await setState(TaskState.STATE_ON);
    if (resp.status !== RcsStatus.RCS_DONE) {
      console.log(`Failed to power on: ${resp.errorMessage}`);
      return false;
    }
    await sleep(100);
  }

  // Set MDI mode
  status = await getStatus();
  task = status.task;
  if (!task) {
    console.log("No task status available");
    return false;
  }
  if (task.taskMode !== TaskMode.MODE_MDI) {
    console.log("Setting MDI mode...");
    const resp = await setMode(TaskMode.MODE_MDI);
    if (resp.status !== RcsStatus.RCS_DONE) {
      console.log(`Failed to set MDI mode: ${resp.errorMessage}`);
      return false;
    }
    await sleep(100);
  }

  return true;
}

async function executeMDI(gcode: string, wait: boolean): Promise<boolean> {
  console.log(`Executing: ${gcode}`);

  const { response, serial } = await mdi(gcode);
  if (response.status === RcsStatus.RCS_ERROR) {
    console.log(`  Error: ${response.errorMessage}`);
    return false;
  }

  if (wait) {
    console.log("  Waiting for completion...");
    const resp = await waitComplete(serial, 60.0);
    if (resp.status === RcsStatus.RCS_ERROR) {
      console.log(`  Error during execution: ${resp.errorMessage}`);
      return false;
    }
    console.log("  Done.");
  }

  return true;
}

async function interactiveMode(): Promise<void> {
  console.log("\nInteractive MDI Mode");
  console.log("Type G-code commands to execute. Type 'quit' or 'exit' to quit.");
  console.log("Type 'status' to show current position.\n");

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  const prompt = (): void => {
    rl.question("MDI> ", async (input) => {
      const cmd = input.trim();

      if (!cmd) {
        prompt();
        return;
      }

      const lower = cmd.toLowerCase();
      if (lower === "quit" || lower === "exit" || lower === "q") {
        rl.close();
        return;
      }

      if (lower === "status") {
        const status = await getStatus();
        const position = status.position;
        if (!position || !position.actualPosition) {
          console.log("No position data available");
          prompt();
          return;
        }
        const pos = position.actualPosition;
        console.log(`Position: X=${pos.x.toFixed(4)} Y=${pos.y.toFixed(4)} Z=${pos.z.toFixed(4)}`);
        prompt();
        return;
      }

      if (lower === "help") {
        console.log("Commands:");
        console.log("  <G-code>  - Execute G-code command");
        console.log("  status    - Show current position");
        console.log("  quit      - Exit interactive mode");
        prompt();
        return;
      }

      try {
        // Ensure we're still in MDI mode
        const status = await getStatus();
        const task = status.task;
        if (task && task.taskMode !== TaskMode.MODE_MDI) {
          if (!(await ensureMDIReady())) {
            console.log("Failed to re-enter MDI mode");
            prompt();
            return;
          }
        }

        await executeMDI(cmd, true);
      } catch (err) {
        console.log(`Error: ${err}`);
      }

      prompt();
    });
  };

  rl.on("close", () => {
    console.log("\nExiting...");
    client.close();
    process.exit(0);
  });

  prompt();
}

async function main(): Promise<void> {
  try {
    // Ensure machine is ready for MDI
    if (!(await ensureMDIReady())) {
      console.log("Could not prepare machine for MDI");
      process.exit(1);
    }

    if (opts.interactive) {
      await interactiveMode();
    } else {
      const success = await executeMDI(command, opts.wait !== false);
      if (!success) {
        process.exit(1);
      }

      // Show final position
      const status = await getStatus();
      const position = status.position;
      if (!position || !position.actualPosition) {
        console.error("No position data available");
      } else {
        const pos = position.actualPosition;
        console.log(`Position: X=${pos.x.toFixed(4)} Y=${pos.y.toFixed(4)} Z=${pos.z.toFixed(4)}`);
      }
      client.close();
    }
  } catch (err) {
    console.error(`Error: ${err}`);
    client.close();
    process.exit(1);
  }
}

main();
