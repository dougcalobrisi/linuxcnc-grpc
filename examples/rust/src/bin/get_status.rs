//! Get LinuxCNC Status Example
//!
//! Connects to the gRPC server and displays the current machine status.
//! This is the simplest example - a good starting point for understanding the API.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin get_status -- --host localhost --port 50051
//! ```

use clap::Parser;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{GetStatusRequest, LinuxCncStatus, TaskMode, TaskState};

#[derive(Parser, Debug)]
#[command(name = "get_status")]
#[command(about = "Get current LinuxCNC machine status")]
struct Args {
    /// gRPC server host
    #[arg(long, default_value = "localhost")]
    host: String,

    /// gRPC server port
    #[arg(long, default_value_t = 50051)]
    port: u16,
}

fn format_task_mode(mode: i32) -> &'static str {
    match TaskMode::try_from(mode) {
        Ok(TaskMode::ModeManual) => "MANUAL",
        Ok(TaskMode::ModeAuto) => "AUTO",
        Ok(TaskMode::ModeMdi) => "MDI",
        _ => "UNKNOWN",
    }
}

fn format_task_state(state: i32) -> &'static str {
    match TaskState::try_from(state) {
        Ok(TaskState::StateEstop) => "ESTOP",
        Ok(TaskState::StateEstopReset) => "ESTOP_RESET",
        Ok(TaskState::StateOn) => "ON",
        Ok(TaskState::StateOff) => "OFF",
        _ => "UNKNOWN",
    }
}

fn print_status(status: &LinuxCncStatus) {
    println!("{}", "=".repeat(60));
    println!("LinuxCNC Status");
    println!("{}", "=".repeat(60));

    // Task status
    if let Some(task) = &status.task {
        println!("\n[Task]");
        println!("  Mode:       {}", format_task_mode(task.task_mode));
        println!("  State:      {}", format_task_state(task.task_state));
        println!("  Exec State: {:?}", task.exec_state);
        println!("  Interp:     {:?}", task.interp_state);
        if !task.file.is_empty() {
            println!("  File:       {}", task.file);
        }
    }

    // Position
    if let Some(position) = &status.position {
        if let Some(pos) = &position.actual_position {
            println!("\n[Position]");
            println!("  X: {:10.4}  Y: {:10.4}  Z: {:10.4}", pos.x, pos.y, pos.z);
            if pos.a != 0.0 || pos.b != 0.0 || pos.c != 0.0 {
                println!("  A: {:10.4}  B: {:10.4}  C: {:10.4}", pos.a, pos.b, pos.c);
            }
        }
    }

    // Trajectory
    if let Some(traj) = &status.trajectory {
        println!("\n[Trajectory]");
        println!("  Enabled:    {}", traj.enabled);
        println!("  Feed Rate:  {:.1}%", traj.feedrate * 100.0);
        println!("  Rapid Rate: {:.1}%", traj.rapidrate * 100.0);
        println!("  Velocity:   {:.2}", traj.current_vel);
    }

    // Joints
    if !status.joints.is_empty() {
        println!("\n[Joints]");
        for joint in &status.joints {
            let homed = if joint.homed { "H" } else { "-" };
            let enabled = if joint.enabled { "E" } else { "-" };
            let fault = if joint.fault { "F" } else { "-" };
            println!(
                "  Joint {}: [{}{}{}] pos={:10.4}",
                joint.joint_number, homed, enabled, fault, joint.input
            );
        }
    }

    // Spindles
    if !status.spindles.is_empty() {
        println!("\n[Spindles]");
        for spindle in &status.spindles {
            let direction = match spindle.direction {
                -1 => "REV",
                1 => "FWD",
                _ => "OFF",
            };
            println!(
                "  Spindle {}: {} @ {:.0} RPM",
                spindle.spindle_number, direction, spindle.speed
            );
        }
    }

    // I/O
    if let Some(io) = &status.io {
        println!("\n[I/O]");
        let estop_str = if io.estop { "ACTIVE" } else { "OK" };
        println!("  E-stop: {}", estop_str);
        println!("  Mist:   {:?}", io.mist);
        println!("  Flood:  {:?}", io.flood);
    }

    // Active G-codes
    if let Some(gcode) = &status.gcode {
        if !gcode.active_gcodes.is_empty() {
            println!("\n[Active G-codes]");
            let gcodes: Vec<String> = gcode
                .active_gcodes
                .iter()
                .filter(|&&g| g > 0)
                .map(|&g| {
                    if g % 10 == 0 {
                        format!("G{}", g / 10)
                    } else {
                        format!("G{:.1}", g as f64 / 10.0)
                    }
                })
                .collect();
            if gcodes.len() > 10 {
                println!("  {}", gcodes[..10].join(" "));
                println!("  {}", gcodes[10..].join(" "));
            } else {
                println!("  {}", gcodes.join(" "));
            }
        }
    }

    // Errors
    if !status.errors.is_empty() {
        println!("\n[Errors]");
        for err in &status.errors {
            println!("  {:?}: {}", err.r#type, err.message);
        }
    }

    println!();
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let addr = format!("http://{}:{}", args.host, args.port);

    let mut client = LinuxCncServiceClient::connect(addr).await?;

    let request = tonic::Request::new(GetStatusRequest {});
    let response = client.get_status(request).await?;
    let status = response.into_inner();

    print_status(&status);

    Ok(())
}
