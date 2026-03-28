//! Stream Status Example
//!
//! Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
//! This is useful for building dashboards or monitoring applications.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin stream_status -- --interval 100
//! ```
//!
//! Press Ctrl+C to stop streaming.

use clap::Parser;
use futures_util::StreamExt;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{LinuxCncStatus, Position, StreamStatusRequest, TaskMode, TaskState};
use std::io::{self, Write};
use std::time::Instant;
use tokio::signal;
use tokio::time::{timeout, Duration};

#[derive(Parser, Debug)]
#[command(name = "stream_status")]
#[command(about = "Stream real-time LinuxCNC status updates")]
struct Args {
    /// gRPC server host
    #[arg(long, default_value = "localhost")]
    host: String,

    /// gRPC server port
    #[arg(long, default_value_t = 50051)]
    port: u16,

    /// Update interval in milliseconds
    #[arg(long, default_value_t = 100)]
    interval: i32,
}

fn format_position(pos: &Position) -> String {
    format!("X:{:8.3} Y:{:8.3} Z:{:8.3}", pos.x, pos.y, pos.z)
}

fn format_state(status: &LinuxCncStatus) -> String {
    let mode = status
        .task
        .as_ref()
        .map(|t| match TaskMode::try_from(t.task_mode) {
            Ok(TaskMode::ModeManual) => "MANUAL",
            Ok(TaskMode::ModeAuto) => "AUTO",
            Ok(TaskMode::ModeMdi) => "MDI",
            _ => "?",
        })
        .unwrap_or("?");

    let state = status
        .task
        .as_ref()
        .map(|t| match TaskState::try_from(t.task_state) {
            Ok(TaskState::StateEstop) => "ESTOP",
            Ok(TaskState::StateEstopReset) => "ESTOP_RESET",
            Ok(TaskState::StateOn) => "ON",
            Ok(TaskState::StateOff) => "OFF",
            _ => "?",
        })
        .unwrap_or("?");

    let interp = status
        .task
        .as_ref()
        .map(|t| format!("{:?}", t.interp_state))
        .unwrap_or_else(|| "?".to_string());

    format!("{}/{}/{}", mode, state, interp)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let addr = format!("http://{}:{}", args.host, args.port);

    let mut client = LinuxCncServiceClient::connect(addr.clone()).await?;

    let request = tonic::Request::new(StreamStatusRequest {
        interval_ms: args.interval,
    });

    let mut stream = client.stream_status(request).await?.into_inner();

    println!(
        "Streaming status from {} (interval: {}ms)",
        addr, args.interval
    );
    println!("Press Ctrl+C to stop\n");
    println!("{}", "-".repeat(80));

    let mut update_count = 0u64;
    let start_time = Instant::now();

    // Set up Ctrl+C handler
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C handler");
    };

    tokio::pin!(ctrl_c);

    loop {
        tokio::select! {
            _ = &mut ctrl_c => {
                break;
            }
            result = timeout(Duration::from_secs(30), stream.next()) => {
                match result {
                    Ok(Some(Ok(status))) => {
                        update_count += 1;

                        let pos_str = status
                            .position
                            .as_ref()
                            .and_then(|p| p.actual_position.as_ref())
                            .map(format_position)
                            .unwrap_or_else(|| "?".to_string());

                        let state = format_state(&status);

                        let vel = status
                            .trajectory
                            .as_ref()
                            .map(|t| t.current_vel)
                            .unwrap_or(0.0);

                        let feed = status
                            .trajectory
                            .as_ref()
                            .map(|t| t.feedrate * 100.0)
                            .unwrap_or(0.0);

                        let spindle_info = status
                            .spindles
                            .first()
                            .filter(|s| s.speed > 0.0)
                            .map(|s| format!(" S:{:.0}", s.speed))
                            .unwrap_or_default();

                        print!(
                            "\r[{:6}] {} | {:20} | V:{:7.2} F:{:5.1}%{}  ",
                            update_count, pos_str, state, vel, feed, spindle_info
                        );
                        io::stdout().flush()?;
                    }
                    Ok(Some(Err(e))) => {
                        eprintln!("\nStream error: {}", e);
                        break;
                    }
                    Ok(None) => {
                        break;
                    }
                    Err(_) => {
                        eprintln!("\nTimeout: no status update received in 30 seconds");
                        break;
                    }
                }
            }
        }
    }

    let elapsed = start_time.elapsed().as_secs_f64();
    println!(
        "\n\nReceived {} updates in {:.1}s ({:.1} updates/sec)",
        update_count,
        elapsed,
        update_count as f64 / elapsed
    );

    Ok(())
}
