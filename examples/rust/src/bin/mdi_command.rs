//! MDI Command Example
//!
//! Execute G-code commands via MDI (Manual Data Input) mode.
//! This is useful for sending individual G-code commands without loading a file.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin mdi_command -- "G0 X10 Y10"
//! cargo run --bin mdi_command -- --interactive
//! ```
//!
//! Before it sends anything the example checks that the machine is on, that
//! every joint is homed and that the interpreter is idle. It does not take the
//! machine out of E-stop, power it on or home it: do that at the machine first.
//! It then switches to MDI mode. The checks are repeated before every command,
//! so in interactive mode a command is not sent while an earlier one is still
//! running.
//!
//! # Safety Warning
//!
//! MDI commands execute immediately on the machine. Understand what
//! each command does before running it. Ctrl+C stops this program, not the
//! machine: a command that was already sent keeps running.

use clap::Parser;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    linux_cnc_command, CommandResponse, GetStatusRequest, InterpState, LinuxCncCommand,
    LinuxCncStatus, MdiCommand, ModeCommand, RcsStatus, TaskMode, TaskState, WaitCompleteRequest,
};
use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};
use tonic::transport::{Channel, Endpoint};

// Deadlines. Every RPC gets one.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const RPC_TIMEOUT: Duration = Duration::from_secs(5); // GetStatus and SendCommand
const STATE_CHANGE_TIMEOUT: Duration = Duration::from_secs(5); // A mode change must finish within this
const WAIT_DEADLINE_MARGIN: Duration = Duration::from_secs(5); // WaitComplete's client deadline = its timeout + this
const POLL_INTERVAL: Duration = Duration::from_millis(50);
const MAX_MDI_TIMEOUT_SECONDS: f64 = 86400.0; // Longest --wait-timeout accepted (one day)

type ExampleResult<T> = Result<T, Box<dyn std::error::Error>>;

#[derive(Parser, Debug)]
#[command(name = "mdi_command")]
#[command(about = "Execute MDI G-code commands")]
struct Args {
    /// gRPC server host
    #[arg(long, default_value = "localhost")]
    host: String,

    /// gRPC server port
    #[arg(long, default_value_t = 50051)]
    port: u16,

    /// Enter interactive MDI mode
    #[arg(long, short)]
    interactive: bool,

    /// Don't wait for command completion
    #[arg(long)]
    no_wait: bool,

    /// Seconds to wait for a command to finish; more than 0 and at most 86400
    #[arg(long, default_value_t = 60.0, allow_negative_numbers = true)]
    wait_timeout: f64,

    /// G-code command to execute
    command: Option<String>,
}

/// Build a request with a deadline. The client enforces it and the server
/// sees it too.
fn with_deadline<T>(message: T, deadline: Duration) -> tonic::Request<T> {
    let mut request = tonic::Request::new(message);
    request.set_timeout(deadline);
    request
}

struct LinuxCncClient {
    client: LinuxCncServiceClient<Channel>,
    serial: AtomicI32,
}

impl LinuxCncClient {
    async fn connect(addr: &str) -> ExampleResult<Self> {
        let channel = Endpoint::from_shared(addr.to_string())?
            .connect_timeout(CONNECT_TIMEOUT)
            .connect()
            .await?;
        Ok(Self {
            client: LinuxCncServiceClient::new(channel),
            serial: AtomicI32::new(0),
        })
    }

    fn next_serial(&self) -> i32 {
        self.serial.fetch_add(1, Ordering::SeqCst) + 1
    }

    async fn get_status(&mut self) -> ExampleResult<LinuxCncStatus> {
        let request = with_deadline(GetStatusRequest {}, RPC_TIMEOUT);
        Ok(self.client.get_status(request).await?.into_inner())
    }

    /// Send a command. `RcsDone` in the response means dispatched, not finished.
    async fn send_command(
        &mut self,
        command: linux_cnc_command::Command,
    ) -> ExampleResult<CommandResponse> {
        let command_message = LinuxCncCommand {
            serial: self.next_serial(),
            timestamp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos() as i64,
            command: Some(command),
            ..Default::default()
        };
        let request = with_deadline(command_message, RPC_TIMEOUT);
        Ok(self.client.send_command(request).await?.into_inner())
    }

    /// Wait until the command that `sent` answers has finished.
    ///
    /// The result is `RcsDone` (finished), `RcsError` (failed) or `RcsExec`
    /// (the timeout expired and the command is still running).
    async fn wait_complete(
        &mut self,
        sent: &CommandResponse,
        timeout: Duration,
    ) -> ExampleResult<CommandResponse> {
        if sent.linuxcnc_serial.is_none() {
            println!("  Note: the server returned no linuxcnc_serial; waiting on the latest command sent by any client instead.");
        }
        let wait_request = WaitCompleteRequest {
            timeout: timeout.as_secs_f64(),
            // Set: wait on this command's serial. The `serial` field would
            // not do that: the server ignores it.
            linuxcnc_serial: sent.linuxcnc_serial,
            ..Default::default()
        };
        // The client deadline must be longer than the wait asked of the server.
        let request = with_deadline(wait_request, timeout + WAIT_DEADLINE_MARGIN);
        Ok(self.client.wait_complete(request).await?.into_inner())
    }

    async fn set_mode(&mut self, mode: TaskMode) -> ExampleResult<CommandResponse> {
        self.send_command(linux_cnc_command::Command::Mode(ModeCommand {
            mode: mode as i32,
        }))
        .await
    }

    /// Send an MDI command.
    async fn mdi(&mut self, gcode: &str) -> ExampleResult<CommandResponse> {
        self.send_command(linux_cnc_command::Command::Mdi(MdiCommand {
            command: gcode.to_string(),
        }))
        .await
    }
}

fn task_mode(status: &LinuxCncStatus) -> Option<i32> {
    status.task.as_ref().map(|task| task.task_mode)
}

/// Check the machine is on, homed and idle, then make sure it is in MDI mode.
async fn ensure_mdi_ready(client: &mut LinuxCncClient) -> ExampleResult<()> {
    let status = client.get_status().await?;
    let task = status.task.as_ref().ok_or("the status has no task section")?;
    let trajectory = status
        .trajectory
        .as_ref()
        .ok_or("the status has no trajectory section")?;

    // Never take the machine out of E-stop or power it on from here.
    if task.task_state != TaskState::StateOn as i32 {
        let state_name = TaskState::try_from(task.task_state)
            .map(|state| state.as_str_name())
            .unwrap_or("unknown");
        return Err(format!(
            "The machine is not on (state {state_name}). Take it out of E-stop and turn it on \
             at the machine, then run this again."
        )
        .into());
    }

    // A 1.0 server lists 16 joint entries, of which only the first
    // trajectory.joints are real. Newer servers list only those.
    let unhomed_joints: Vec<i32> = status
        .joints
        .iter()
        .take(trajectory.joints.max(0) as usize)
        .filter(|joint| !joint.homed)
        .map(|joint| joint.joint_number)
        .collect();
    if !unhomed_joints.is_empty() {
        return Err(format!(
            "Joints {unhomed_joints:?} are not homed. Home the machine first; this example \
             does not home it."
        )
        .into());
    }

    // Checked every time, also when the machine is already in MDI mode: a
    // command whose wait timed out is still running, and so may a program be.
    if task.interp_state != InterpState::InterpIdle as i32 {
        let interp_state_name = InterpState::try_from(task.interp_state)
            .map(|state| state.as_str_name())
            .unwrap_or("unknown");
        return Err(format!(
            "The interpreter is still busy with a previous command or a running program \
             (state {interp_state_name}). Nothing was sent; wait for it to finish and try again."
        )
        .into());
    }

    if task.task_mode == TaskMode::ModeMdi as i32 {
        return Ok(());
    }

    println!("Setting MDI mode...");
    let sent = client.set_mode(TaskMode::ModeMdi).await?;
    if sent.status != RcsStatus::RcsDone as i32 {
        return Err(format!("MDI mode was refused: {}", sent.error_message).into());
    }

    let finished = client.wait_complete(&sent, STATE_CHANGE_TIMEOUT).await?;
    if finished.status == RcsStatus::RcsExec as i32 {
        return Err(format!("MDI mode had not finished after {STATE_CHANGE_TIMEOUT:?}").into());
    }
    if finished.status != RcsStatus::RcsDone as i32 {
        return Err(format!("MDI mode failed: {}", finished.error_message).into());
    }

    // Finished is not the same as in effect: check the status, with a deadline.
    let deadline = Instant::now() + STATE_CHANGE_TIMEOUT;
    while task_mode(&client.get_status().await?) != Some(TaskMode::ModeMdi as i32) {
        if Instant::now() >= deadline {
            return Err(format!(
                "MDI mode did not show in the status within {STATE_CHANGE_TIMEOUT:?}"
            )
            .into());
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
    Ok(())
}

/// Send an MDI command and, if asked, wait until it has finished.
async fn execute_mdi(
    client: &mut LinuxCncClient,
    gcode: &str,
    wait: bool,
    wait_timeout: Duration,
) -> ExampleResult<()> {
    println!("Executing: {}", gcode);

    let sent = client.mdi(gcode).await?;
    if sent.status != RcsStatus::RcsDone as i32 {
        return Err(format!("The command was refused: {}", sent.error_message).into());
    }

    if !wait {
        println!("  Dispatched (not waiting for it to finish).");
        return Ok(());
    }

    println!("  Waiting for completion...");
    let finished = client.wait_complete(&sent, wait_timeout).await?;
    if finished.status == RcsStatus::RcsDone as i32 {
        println!("  Done.");
        Ok(())
    } else if finished.status == RcsStatus::RcsExec as i32 {
        Err(format!(
            "Timed out: the command was still running after {wait_timeout:?}. It has not been \
             cancelled."
        )
        .into())
    } else {
        Err(format!("The command failed: {}", finished.error_message).into())
    }
}

async fn print_position(client: &mut LinuxCncClient) -> ExampleResult<()> {
    let status = client.get_status().await?;
    let position = status
        .position
        .as_ref()
        .and_then(|position_status| position_status.actual_position.as_ref())
        .ok_or("the status has no position")?;
    println!(
        "Position: X={:.4} Y={:.4} Z={:.4}",
        position.x, position.y, position.z
    );
    Ok(())
}

/// Interactive MDI shell. A command that fails does not end the session, but
/// it is counted, and the count comes back as an error so the program exits
/// non-zero. So does a failure to read the input.
async fn interactive_mode(client: &mut LinuxCncClient, wait_timeout: Duration) -> ExampleResult<()> {
    let mut failed_command_count = 0;
    println!("\nInteractive MDI Mode");
    println!("Type G-code commands to execute. Type 'quit' or 'exit' to quit.");
    println!("Type 'status' to show current position.\n");

    let mut input_lines = io::stdin().lock().lines();
    loop {
        print!("MDI> ");
        io::stdout().flush()?;

        let Some(input_line) = input_lines.next() else {
            println!("\nExiting...");
            break;
        };
        // A read error is not the end of the input: say so and fail.
        let line = input_line
            .map_err(|read_error| format!("Reading input failed: {read_error}"))?
            .trim()
            .to_string();
        if line.is_empty() {
            continue;
        }

        let lower = line.to_lowercase();
        if lower == "quit" || lower == "exit" || lower == "q" {
            break;
        }

        if lower == "help" {
            println!("Commands:");
            println!("  <G-code>  - Execute G-code command");
            println!("  status    - Show current position");
            println!("  quit      - Exit interactive mode");
            continue;
        }

        if lower == "status" {
            if let Err(error) = print_position(client).await {
                failed_command_count += 1;
                println!("  Error: {error}");
            }
            continue;
        }

        // The machine may have been turned off or switched to another mode
        // since the last command, or the last command may still be running.
        if let Err(error) = ensure_mdi_ready(client).await {
            failed_command_count += 1;
            println!("  Error: {error}");
            continue;
        }
        if let Err(error) = execute_mdi(client, &line, true, wait_timeout).await {
            failed_command_count += 1;
            println!("  Error: {error}");
        }
    }

    if failed_command_count > 0 {
        return Err(format!("{failed_command_count} command(s) failed in this session.").into());
    }
    Ok(())
}

fn print_usage() {
    println!("Usage: mdi_command [OPTIONS] [COMMAND]");
    println!();
    println!("Examples:");
    println!("  mdi_command \"G0 X10 Y10\"");
    println!("  mdi_command \"G1 X20 F100\"");
    println!("  mdi_command --interactive");
}

async fn run(args: Args) -> ExampleResult<()> {
    // Checked before any RPC: 0 would make the server use its own default, and
    // a negative or huge value would give a client deadline that has already
    // expired or cannot be represented, after the command was dispatched.
    if !(args.wait_timeout.is_finite()
        && args.wait_timeout > 0.0
        && args.wait_timeout <= MAX_MDI_TIMEOUT_SECONDS)
    {
        return Err(
            "--wait-timeout must be a number of seconds greater than 0 and at most 86400".into(),
        );
    }
    let wait_timeout = Duration::from_secs_f64(args.wait_timeout);

    let addr = format!("http://{}:{}", args.host, args.port);
    let mut client = LinuxCncClient::connect(&addr).await?;

    ensure_mdi_ready(&mut client).await?;

    if args.interactive {
        interactive_mode(&mut client, wait_timeout).await?;
    } else if let Some(command) = args.command {
        execute_mdi(&mut client, &command, !args.no_wait, wait_timeout).await?;
        print_position(&mut client).await?;
    }

    Ok(())
}

#[tokio::main]
async fn main() {
    let args = Args::parse();

    if args.command.is_none() && !args.interactive {
        print_usage();
        std::process::exit(1);
    }

    if let Err(error) = run(args).await {
        eprintln!("Error: {error}");
        std::process::exit(1);
    }
}
