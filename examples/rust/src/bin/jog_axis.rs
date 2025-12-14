//! Jog Axis Example
//!
//! Demonstrates jogging an axis using the LinuxCNC gRPC server.
//! Supports both continuous jogging and incremental jogging.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin jog_axis -- --host localhost --port 50051 [--skip-demo]
//! ```
//!
//! # Safety Warning
//!
//! This script moves the machine! Ensure you have clear access to E-stop
//! and understand the jog parameters before running.

use clap::Parser;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    linux_cnc_command, GetStatusRequest, JogCommand, JogType, LinuxCncCommand, LinuxCncStatus,
    ModeCommand, RcsStatus, StateCommand, TaskMode, TaskState,
};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::Duration;

#[derive(Parser, Debug)]
#[command(name = "jog_axis")]
#[command(about = "Jog machine axes via gRPC")]
struct Args {
    /// gRPC server host
    #[arg(long, default_value = "localhost")]
    host: String,

    /// gRPC server port
    #[arg(long, default_value_t = 50051)]
    port: u16,

    /// Skip demo movements (just show status)
    #[arg(long)]
    skip_demo: bool,
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
    ) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        let cmd = LinuxCncCommand {
            serial: self.next_serial(),
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
        Ok(RcsStatus::try_from(resp.status).unwrap_or(RcsStatus::RcsDone))
    }

    async fn set_mode(&mut self, mode: TaskMode) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        self.send_command(linux_cnc_command::Command::Mode(ModeCommand {
            mode: mode as i32,
        }))
        .await
    }

    async fn set_state(
        &mut self,
        state: TaskState,
    ) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        self.send_command(linux_cnc_command::Command::State(StateCommand {
            state: state as i32,
        }))
        .await
    }

    async fn jog_continuous(
        &mut self,
        axis: i32,
        velocity: f64,
    ) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        self.send_command(linux_cnc_command::Command::Jog(JogCommand {
            r#type: JogType::JogContinuous as i32,
            is_joint: false,
            index: axis,
            velocity,
            increment: 0.0,
        }))
        .await
    }

    async fn jog_increment(
        &mut self,
        axis: i32,
        velocity: f64,
        increment: f64,
    ) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        self.send_command(linux_cnc_command::Command::Jog(JogCommand {
            r#type: JogType::JogIncrement as i32,
            is_joint: false,
            index: axis,
            velocity,
            increment,
        }))
        .await
    }

    async fn jog_stop(&mut self, axis: i32) -> Result<RcsStatus, Box<dyn std::error::Error>> {
        self.send_command(linux_cnc_command::Command::Jog(JogCommand {
            r#type: JogType::JogStop as i32,
            is_joint: false,
            index: axis,
            velocity: 0.0,
            increment: 0.0,
        }))
        .await
    }
}

async fn ensure_machine_ready(client: &mut LinuxCncClient) -> Result<(), Box<dyn std::error::Error>> {
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

    // Set manual mode for jogging
    let status = client.get_status().await?;
    if let Some(task) = &status.task {
        if task.task_mode != TaskMode::ModeManual as i32 {
            println!("Setting manual mode...");
            client.set_mode(TaskMode::ModeManual).await?;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    Ok(())
}

async fn demo_incremental_jog(
    client: &mut LinuxCncClient,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("\n--- Incremental Jog Demo ---");
    println!("Jogging X axis +1.0 units...");

    // Jog X axis positive by 1.0 unit at 100 units/min
    client.jog_increment(0, 100.0, 1.0).await?;

    // Wait for motion to complete
    tokio::time::sleep(Duration::from_secs(1)).await;

    // Show new position
    let status = client.get_status().await?;
    if let Some(pos) = status.position.as_ref().and_then(|p| p.actual_position.as_ref()) {
        println!("New position: X={:.4} Y={:.4} Z={:.4}", pos.x, pos.y, pos.z);
    }

    Ok(())
}

async fn demo_continuous_jog(
    client: &mut LinuxCncClient,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("\n--- Continuous Jog Demo ---");
    println!("Jogging Y axis positive for 0.5 seconds...");

    // Start continuous jog on Y axis at 50 units/min
    client.jog_continuous(1, 50.0).await?;

    // Let it jog for a bit
    tokio::time::sleep(Duration::from_millis(500)).await;

    // Stop the jog
    println!("Stopping jog...");
    client.jog_stop(1).await?;

    // Show new position
    let status = client.get_status().await?;
    if let Some(pos) = status.position.as_ref().and_then(|p| p.actual_position.as_ref()) {
        println!("New position: X={:.4} Y={:.4} Z={:.4}", pos.x, pos.y, pos.z);
    }

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let addr = format!("http://{}:{}", args.host, args.port);

    let mut client = LinuxCncClient::connect(&addr).await?;

    // Show initial status
    let status = client.get_status().await?;
    if let Some(pos) = status.position.as_ref().and_then(|p| p.actual_position.as_ref()) {
        println!("Current position: X={:.4} Y={:.4} Z={:.4}", pos.x, pos.y, pos.z);
    }

    if args.skip_demo {
        println!("Skipping demo movements (--skip-demo)");
        return Ok(());
    }

    // Ensure machine is ready for jogging
    ensure_machine_ready(&mut client).await?;

    // Run demos
    demo_incremental_jog(&mut client).await?;
    demo_continuous_jog(&mut client).await?;

    println!("\nJog demo complete!");

    Ok(())
}
