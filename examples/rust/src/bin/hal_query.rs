//! HAL Query Example
//!
//! Query HAL (Hardware Abstraction Layer) pins, signals, and parameters.
//! Useful for debugging HAL configurations and monitoring I/O.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin hal_query -- pins "axis.*"
//! cargo run --bin hal_query -- signals
//! cargo run --bin hal_query -- components
//! cargo run --bin hal_query -- watch spindle.0.speed-out axis.x.pos-cmd
//! ```

use clap::{Parser, Subcommand};
use futures_util::StreamExt;
use linuxcnc_grpc::hal::hal_service_client::HalServiceClient;
use linuxcnc_grpc::hal::{
    GetSystemStatusRequest, HalType, HalValue, ParamDirection, PinDirection, QueryComponentsCommand,
    QueryParamsCommand, QueryPinsCommand, QuerySignalsCommand, WatchRequest,
};
use std::time::{Duration, UNIX_EPOCH};
use tokio::signal;
use tokio::time::timeout;

#[derive(Parser, Debug)]
#[command(name = "hal_query")]
#[command(about = "Query HAL pins, signals, parameters, and components")]
struct Args {
    /// gRPC server host
    #[arg(long, default_value = "localhost")]
    host: String,

    /// gRPC server port
    #[arg(long, default_value_t = 50051)]
    port: u16,

    /// Update interval in ms for watch command
    #[arg(long, default_value_t = 500)]
    interval: i32,

    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Query HAL pins
    Pins {
        /// Glob pattern to match pin names
        #[arg(default_value = "*")]
        pattern: String,
    },
    /// Query HAL signals
    Signals {
        /// Glob pattern to match signal names
        #[arg(default_value = "*")]
        pattern: String,
    },
    /// Query HAL parameters
    Params {
        /// Glob pattern to match parameter names
        #[arg(default_value = "*")]
        pattern: String,
    },
    /// Query HAL components
    Components {
        /// Glob pattern to match component names
        #[arg(default_value = "*")]
        pattern: String,
    },
    /// Watch values for changes
    Watch {
        /// Names of pins/signals/params to watch
        names: Vec<String>,
    },
    /// Get HAL system status
    Status,
}

fn format_value(value: &Option<HalValue>) -> String {
    match value {
        Some(v) => match &v.value {
            Some(linuxcnc_grpc::hal::hal_value::Value::BitValue(b)) => {
                if *b { "TRUE" } else { "FALSE" }.to_string()
            }
            Some(linuxcnc_grpc::hal::hal_value::Value::FloatValue(f)) => format!("{:.6}", f),
            Some(linuxcnc_grpc::hal::hal_value::Value::S32Value(i)) => format!("{}", i),
            Some(linuxcnc_grpc::hal::hal_value::Value::U32Value(u)) => format!("{}", u),
            Some(linuxcnc_grpc::hal::hal_value::Value::S64Value(i)) => format!("{}", i),
            Some(linuxcnc_grpc::hal::hal_value::Value::U64Value(u)) => format!("{}", u),
            Some(linuxcnc_grpc::hal::hal_value::Value::PortValue(s)) => s.clone(),
            None => "?".to_string(),
        },
        None => "?".to_string(),
    }
}

fn format_type(hal_type: i32) -> &'static str {
    match HalType::try_from(hal_type) {
        Ok(HalType::HalBit) => "BIT",
        Ok(HalType::HalFloat) => "FLOAT",
        Ok(HalType::HalS32) => "S32",
        Ok(HalType::HalU32) => "U32",
        Ok(HalType::HalS64) => "S64",
        Ok(HalType::HalU64) => "U64",
        Ok(HalType::HalPort) => "PORT",
        _ => "?",
    }
}

fn format_direction(direction: i32) -> &'static str {
    match PinDirection::try_from(direction) {
        Ok(PinDirection::HalIn) => "IN",
        Ok(PinDirection::HalOut) => "OUT",
        Ok(PinDirection::HalIo) => "IO",
        _ => "?",
    }
}

async fn query_pins(
    client: &mut HalServiceClient<tonic::transport::Channel>,
    pattern: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let response = client
        .query_pins(tonic::Request::new(QueryPinsCommand {
            pattern: pattern.to_string(),
        }))
        .await?
        .into_inner();

    if !response.success {
        eprintln!("Error: {}", response.error);
        return Ok(());
    }

    println!("Found {} pins matching '{}':\n", response.pins.len(), pattern);
    println!(
        "{:<50} {:<6} {:<4} {:<15} {}",
        "Name", "Type", "Dir", "Value", "Signal"
    );
    println!("{}", "-".repeat(90));

    let mut pins = response.pins;
    pins.sort_by(|a, b| a.name.cmp(&b.name));

    for pin in pins {
        let direction = format_direction(pin.direction);
        let value = format_value(&pin.value);
        let pin_type = format_type(pin.r#type);
        let signal = if pin.signal.is_empty() {
            "-"
        } else {
            &pin.signal
        };
        println!(
            "{:<50} {:<6} {:<4} {:<15} {}",
            pin.name, pin_type, direction, value, signal
        );
    }

    Ok(())
}

async fn query_signals(
    client: &mut HalServiceClient<tonic::transport::Channel>,
    pattern: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let response = client
        .query_signals(tonic::Request::new(QuerySignalsCommand {
            pattern: pattern.to_string(),
        }))
        .await?
        .into_inner();

    if !response.success {
        eprintln!("Error: {}", response.error);
        return Ok(());
    }

    println!(
        "Found {} signals matching '{}':\n",
        response.signals.len(),
        pattern
    );
    println!(
        "{:<40} {:<6} {:<15} {:<30} {}",
        "Name", "Type", "Value", "Driver", "Readers"
    );
    println!("{}", "-".repeat(100));

    let mut signals = response.signals;
    signals.sort_by(|a, b| a.name.cmp(&b.name));

    for sig in signals {
        let value = format_value(&sig.value);
        let sig_type = format_type(sig.r#type);
        let driver = if sig.driver.is_empty() {
            "(none)"
        } else {
            &sig.driver
        };
        let readers = if sig.reader_count > 0 {
            format!("{} readers", sig.reader_count)
        } else {
            "-".to_string()
        };
        println!(
            "{:<40} {:<6} {:<15} {:<30} {}",
            sig.name, sig_type, value, driver, readers
        );
    }

    Ok(())
}

async fn query_params(
    client: &mut HalServiceClient<tonic::transport::Channel>,
    pattern: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let response = client
        .query_params(tonic::Request::new(QueryParamsCommand {
            pattern: pattern.to_string(),
        }))
        .await?
        .into_inner();

    if !response.success {
        eprintln!("Error: {}", response.error);
        return Ok(());
    }

    println!(
        "Found {} parameters matching '{}':\n",
        response.params.len(),
        pattern
    );
    println!("{:<50} {:<6} {:<4} {}", "Name", "Type", "Mode", "Value");
    println!("{}", "-".repeat(80));

    let mut params = response.params;
    params.sort_by(|a, b| a.name.cmp(&b.name));

    for param in params {
        let value = format_value(&param.value);
        let param_type = format_type(param.r#type);
        let mode = match ParamDirection::try_from(param.direction) {
            Ok(ParamDirection::HalRw) => "RW",
            _ => "RO",
        };
        println!("{:<50} {:<6} {:<4} {}", param.name, param_type, mode, value);
    }

    Ok(())
}

async fn query_components(
    client: &mut HalServiceClient<tonic::transport::Channel>,
    pattern: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let response = client
        .query_components(tonic::Request::new(QueryComponentsCommand {
            pattern: pattern.to_string(),
        }))
        .await?
        .into_inner();

    if !response.success {
        eprintln!("Error: {}", response.error);
        return Ok(());
    }

    println!(
        "Found {} components matching '{}':\n",
        response.components.len(),
        pattern
    );
    println!(
        "{:<30} {:<6} {:<6} {:<6} {}",
        "Name", "ID", "Ready", "Pins", "Params"
    );
    println!("{}", "-".repeat(60));

    let mut comps = response.components;
    comps.sort_by(|a, b| a.name.cmp(&b.name));

    for comp in comps {
        let ready = if comp.ready { "Yes" } else { "No" };
        println!(
            "{:<30} {:<6} {:<6} {:<6} {}",
            comp.name,
            comp.id,
            ready,
            comp.pins.len(),
            comp.params.len()
        );
    }

    Ok(())
}

async fn watch_values(
    client: &mut HalServiceClient<tonic::transport::Channel>,
    names: Vec<String>,
    interval_ms: i32,
) -> Result<(), Box<dyn std::error::Error>> {
    let request = tonic::Request::new(WatchRequest {
        names: names.clone(),
        interval_ms,
    });

    let mut stream = client.watch_values(request).await?.into_inner();

    println!("Watching {} values (interval: {}ms)", names.len(), interval_ms);
    println!("Press Ctrl+C to stop\n");

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
                    Ok(Some(Ok(batch))) => {
                        for change in batch.changes {
                            let old_val = format_value(&change.old_value);
                            let new_val = format_value(&change.new_value);
                            let ts = UNIX_EPOCH + Duration::from_nanos(change.timestamp as u64);
                            let datetime = chrono_lite_format(ts);
                            println!("[{}] {}: {} -> {}", datetime, change.name, old_val, new_val);
                        }
                    }
                    Ok(Some(Err(e))) => {
                        eprintln!("Stream error: {}", e);
                        break;
                    }
                    Ok(None) => {
                        break;
                    }
                    Err(_) => {
                        eprintln!("Timeout: no update received in 30 seconds");
                        break;
                    }
                }
            }
        }
    }

    Ok(())
}

fn chrono_lite_format(ts: std::time::SystemTime) -> String {
    let duration = ts.duration_since(UNIX_EPOCH).unwrap_or_default();
    let total_secs = duration.as_secs();

    // Days since Unix epoch to Y/M/D (simplified Gregorian)
    let mut days = (total_secs / 86400) as i64;
    let secs_of_day = total_secs % 86400;
    let hours = secs_of_day / 3600;
    let minutes = (secs_of_day % 3600) / 60;
    let seconds = secs_of_day % 60;

    // Compute year/month/day from days since 1970-01-01 (UTC)
    let mut year: i64 = 1970;
    loop {
        let days_in_year = if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) { 366 } else { 365 };
        if days < days_in_year {
            break;
        }
        days -= days_in_year;
        year += 1;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days_in_months = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let mut month = 0usize;
    for &dim in &days_in_months {
        if days < dim {
            break;
        }
        days -= dim;
        month += 1;
    }

    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02} UTC", year, month + 1, days + 1, hours, minutes, seconds)
}

async fn get_system_status(
    client: &mut HalServiceClient<tonic::transport::Channel>,
) -> Result<(), Box<dyn std::error::Error>> {
    let response = client
        .get_system_status(tonic::Request::new(GetSystemStatusRequest {}))
        .await?
        .into_inner();

    println!("HAL System Status");
    println!("{}", "=".repeat(40));
    println!("Pins:       {}", response.pins.len());
    println!("Signals:    {}", response.signals.len());
    println!("Parameters: {}", response.params.len());
    println!("Components: {}", response.components.len());
    println!("Simulation: {}", response.is_sim);
    println!("Real-time:  {}", response.is_rt);
    println!("Userspace:  {}", response.is_userspace);
    if !response.kernel_version.is_empty() {
        println!("Kernel:     {}", response.kernel_version);
    }

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let addr = format!("http://{}:{}", args.host, args.port);

    let mut client = HalServiceClient::connect(addr).await?;

    match args.command {
        Command::Pins { pattern } => query_pins(&mut client, &pattern).await?,
        Command::Signals { pattern } => query_signals(&mut client, &pattern).await?,
        Command::Params { pattern } => query_params(&mut client, &pattern).await?,
        Command::Components { pattern } => query_components(&mut client, &pattern).await?,
        Command::Watch { names } => {
            if names.is_empty() {
                eprintln!("Error: watch command requires at least one name");
                std::process::exit(1);
            }
            watch_values(&mut client, names, args.interval).await?
        }
        Command::Status => get_system_status(&mut client).await?,
    }

    Ok(())
}
