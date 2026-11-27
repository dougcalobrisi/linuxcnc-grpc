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
//! `--interval` is the time between updates in milliseconds, from 1 to 60000.
//!
//! Press Ctrl+C to stop streaming. The example gives up, and exits non-zero,
//! when no update has arrived for 30 seconds or for twice the interval plus 5
//! seconds, whichever is longer. It also exits non-zero when the stream fails
//! or the server cancels it.

use clap::Parser;
use futures_util::StreamExt;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    InterpState, LinuxCncStatus, Position, StreamStatusRequest, TaskMode, TaskState,
};
use std::io::{self, Write};
use std::time::Instant;
use tokio::signal;
use tokio::time::{timeout, Duration};
use tonic::transport::Endpoint;

// The server accepts intervals up to 60 seconds.
const MIN_INTERVAL_MS: i32 = 1;
const MAX_INTERVAL_MS: i32 = 60000;

// A stream has no overall deadline: it runs until Ctrl+C. Instead, give up
// when the server has sent nothing for too long: the larger of
// MIN_STREAM_IDLE_TIMEOUT and STREAM_IDLE_INTERVALS update intervals plus
// STREAM_IDLE_MARGIN.
const MIN_STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(30);
const STREAM_IDLE_INTERVALS: u32 = 2;
const STREAM_IDLE_MARGIN: Duration = Duration::from_secs(5);

/// The time without an update after which the stream counts as dead.
fn stream_idle_timeout(interval_ms: i32) -> Duration {
    let update_interval = Duration::from_millis(interval_ms.max(0) as u64);
    MIN_STREAM_IDLE_TIMEOUT.max(update_interval * STREAM_IDLE_INTERVALS + STREAM_IDLE_MARGIN)
}

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

    /// Update interval in milliseconds, 1 to 60000. The example gives up
    /// when no update arrives for 30 seconds or twice the interval plus 5
    /// seconds, whichever is longer
    #[arg(long, default_value_t = 100, allow_negative_numbers = true)]
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
        .map(|t| match InterpState::try_from(t.interp_state) {
            Ok(InterpState::InterpIdle) => "IDLE",
            Ok(InterpState::InterpReading) => "READING",
            Ok(InterpState::InterpPaused) => "PAUSED",
            Ok(InterpState::InterpWaiting) => "WAITING",
            _ => "?",
        })
        .unwrap_or("?");

    format!("{}/{}/{}", mode, state, interp)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();

    if !(MIN_INTERVAL_MS..=MAX_INTERVAL_MS).contains(&args.interval) {
        eprintln!("Error: --interval must be a whole number of milliseconds from 1 to 60000");
        std::process::exit(1);
    }
    let idle_timeout = stream_idle_timeout(args.interval);

    let addr = format!("http://{}:{}", args.host, args.port);

    let channel = Endpoint::from_shared(addr.clone())?
        .connect_timeout(Duration::from_secs(5))
        .connect()
        .await?;
    let mut client = LinuxCncServiceClient::new(channel);

    let request = tonic::Request::new(StreamStatusRequest {
        interval_ms: args.interval,
    });

    println!(
        "Streaming status from {} (interval: {}ms)",
        addr, args.interval
    );
    println!("Press Ctrl+C to stop\n");
    println!("{}", "-".repeat(80));

    let mut update_count = 0u64;
    let start_time = Instant::now();
    let mut stream_failed = false;
    let mut stream_went_silent = false;

    // Set up Ctrl+C handler
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C handler");
    };

    tokio::pin!(ctrl_c);

    // Opening the stream waits for the server's response headers, and a
    // server may send those only together with the first update. So this
    // await is under the idle timeout and Ctrl+C as well: a server that
    // accepts the connection and then never answers must not hang the
    // program.
    let opened_stream = tokio::select! {
        _ = &mut ctrl_c => None,
        opened = timeout(idle_timeout, client.stream_status(request)) => {
            match opened {
                Ok(Ok(response)) => Some(response.into_inner()),
                Ok(Err(open_error)) => {
                    eprintln!("\nStream error: {}", open_error);
                    stream_failed = true;
                    None
                }
                Err(_) => {
                    stream_went_silent = true;
                    None
                }
            }
        }
    };

    if let Some(mut stream) = opened_stream {
        loop {
            tokio::select! {
                _ = &mut ctrl_c => {
                    break;
                }
                result = timeout(idle_timeout, stream.next()) => {
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

                        Ok(Some(Err(stream_error))) => {
                            // Includes a stream the server cancelled.
                            eprintln!("\nStream error: {}", stream_error);
                            stream_failed = true;
                            break;
                        }
                        Ok(None) => {
                            break;
                        }
                        Err(_) => {
                            stream_went_silent = true;
                            break;
                        }
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

    if stream_went_silent {
        eprintln!(
            "No status update for {} seconds; giving up.",
            idle_timeout.as_secs_f64()
        );
        std::process::exit(1);
    }
    if stream_failed {
        std::process::exit(1);
    }
    Ok(())
}
