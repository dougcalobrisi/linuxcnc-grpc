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
//! # Safety Warning
//!
//! MDI commands execute immediately on the machine. Understand what
//! each command does before running it.

use clap::Parser;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    linux_cnc_command, GetStatusRequest, LinuxCncCommand, LinuxCncStatus, MdiCommand, ModeCommand,
    RcsStatus, StateCommand, TaskMode, TaskState, WaitCompleteRequest,
};
use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::Duration;

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

    /// G-code command to execute
    command: Option<String>,
}

struct LinuxCncClient {
    client: LinuxCncServiceClient<tonic::transport::Channel>,
    serial: AtomicI32,
}

impl LinuxCncClient {
    async fn connect(addr: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let client = LinuxCncServiceClient::connect(addr.to_string()).await?;
        Ok(Self {
            client,
            serial: AtomicI32::new(0),
        })
    }

    fn next_serial(&self) -> i32 {
        self.serial.fetch_add(1, Ordering::SeqCst) + 1
    }

    async fn get_status(&mut self) -> Result<LinuxCncStatus, Box<dyn std::error::Error>> {
        let request = tonic::Request::new(GetStatusRequest {});
        let response = self.client.get_status(request).await?;
        Ok(response.into_inner())
    }

    async fn send_command(
        &mut self,
        command: linux_cnc_command::Command,
    ) -> Result<(RcsStatus, i32), Box<dyn std::error::Error>> {
        let serial = self.next_serial();
        let cmd = LinuxCncCommand {
            serial,
            timestamp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos() as i64,
            command: Some(command),
        };
        let response = self.client.send_command(tonic::Request::new(cmd)).await?;
        let resp = response.into_inner();
        if resp.status == RcsStatus::RcsError as i32 {
            return Err(resp.error_message.into());
        }
        Ok((
            RcsStatus::try_from(resp.status).unwrap_or(RcsStatus::RcsDone),
            serial,
        ))
    }

    async fn wait_complete(
        &mut self,
        serial: i32,
        timeout: f64,
    ) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        let request = tonic::Request::new(WaitCompleteRequest { serial, timeout });
        let response = self.client.wait_complete(request).await?;
        let resp = response.into_inner();
        if resp.status == RcsStatus::RcsError as i32 {
            return Err(resp.error_message.into());
        }
        Ok(RcsStatus::try_from(resp.status).unwrap_or(RcsStatus::RcsDone))
    }

    async fn set_mode(&mut self, mode: TaskMode) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        let (status, _) = self
            .send_command(linux_cnc_command::Command::Mode(ModeCommand {
                mode: mode as i32,
            }))
            .await?;
        Ok(status)
    }

    async fn set_state(
        &mut self,
        state: TaskState,
    ) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        let (status, _) = self
            .send_command(linux_cnc_command::Command::State(StateCommand {
                state: state as i32,
            }))
            .await?;
        Ok(status)
    }

    async fn mdi(&mut self, gcode: &str) -> Result<(RcsStatus, i32), Box<dyn std::error::Error>> {
        self.send_command(linux_cnc_command::Command::Mdi(MdiCommand {
            command: gcode.to_string(),
        }))
        .await
    }
}

async fn ensure_mdi_ready(client: &mut LinuxCncClient) -> Result<(), Box<dyn std::error::Error>> {
    let status = client.get_status().await?;

    // Check E-stop
    if let Some(task) = &status.task {
        if task.task_state == TaskState::StateEstop as i32 {
            println!("Machine is in E-stop. Resetting...");
            client.set_state(TaskState::StateEstopReset).await?;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    // Power on
    let status = client.get_status().await?;
    if let Some(task) = &status.task {
        if task.task_state != TaskState::StateOn as i32 {
            println!("Powering on machine...");
            client.set_state(TaskState::StateOn).await?;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    // Set MDI mode
    let status = client.get_status().await?;
    if let Some(task) = &status.task {
        if task.task_mode != TaskMode::ModeMdi as i32 {
            println!("Setting MDI mode...");
            client.set_mode(TaskMode::ModeMdi).await?;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    Ok(())
}

async fn execute_mdi(
    client: &mut LinuxCncClient,
    gcode: &str,
    wait: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("Executing: {}", gcode);

    let (status, serial) = client.mdi(gcode).await?;
    if status == RcsStatus::RcsError {
        return Err("MDI command failed".into());
    }

    if wait {
        println!("  Waiting for completion...");
        let status = client.wait_complete(serial, 60.0).await?;
        if status == RcsStatus::RcsError {
            return Err("Error during execution".into());
        }
        println!("  Done.");
    }

    Ok(())
}

async fn interactive_mode(client: &mut LinuxCncClient) -> Result<(), Box<dyn std::error::Error>> {
    println!("\nInteractive MDI Mode");
    println!("Type G-code commands to execute. Type 'quit' or 'exit' to quit.");
    println!("Type 'status' to show current position.\n");

    let stdin = io::stdin();
    let handle = stdin.lock();

    for line in handle.lines() {
        print!("MDI> ");
        io::stdout().flush()?;

        let cmd = line?.trim().to_string();
        if cmd.is_empty() {
            continue;
        }

        let lower = cmd.to_lowercase();
        if lower == "quit" || lower == "exit" || lower == "q" {
            break;
        }

        if lower == "status" {
            let status = client.get_status().await?;
            if let Some(pos) = status.position.as_ref().and_then(|p| p.actual_position.as_ref()) {
                println!("Position: X={:.4} Y={:.4} Z={:.4}", pos.x, pos.y, pos.z);
            }
            continue;
        }

        if lower == "help" {
            println!("Commands:");
            println!("  <G-code>  - Execute G-code command");
            println!("  status    - Show current position");
            println!("  quit      - Exit interactive mode");
            continue;
        }

        // Ensure we're still in MDI mode
        let status = client.get_status().await?;
        if let Some(task) = &status.task {
            if task.task_mode != TaskMode::ModeMdi as i32 {
                if let Err(e) = ensure_mdi_ready(client).await {
                    eprintln!("Failed to re-enter MDI mode: {}", e);
                    continue;
                }
            }
        }

        if let Err(e) = execute_mdi(client, &cmd, true).await {
            eprintln!("Error: {}", e);
        }
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

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();

    if args.command.is_none() && !args.interactive {
        print_usage();
        std::process::exit(1);
    }

    let addr = format!("http://{}:{}", args.host, args.port);
    let mut client = LinuxCncClient::connect(&addr).await?;

    // Ensure machine is ready for MDI
    ensure_mdi_ready(&mut client).await?;

    if args.interactive {
        interactive_mode(&mut client).await?;
    } else if let Some(command) = args.command {
        execute_mdi(&mut client, &command, !args.no_wait).await?;

        // Show final position
        let status = client.get_status().await?;
        if let Some(pos) = status.position.as_ref().and_then(|p| p.actual_position.as_ref()) {
            println!("Position: X={:.4} Y={:.4} Z={:.4}", pos.x, pos.y, pos.z);
        }
    }

    Ok(())
}
