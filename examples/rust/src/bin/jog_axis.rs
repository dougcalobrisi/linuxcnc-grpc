//! Jog Axis Example
//!
//! Jogs the X axis by a fixed increment, then jogs the Y axis continuously for
//! half a second and stops it.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin jog_axis -- --host localhost --port 50051 [--skip-demo]
//! ```
//!
//! Before it moves anything the example checks that the machine is on, that
//! every joint is homed and that the interpreter is idle. It does not take the
//! machine out of E-stop, power it on or home it: do that at the machine first.
//! It then switches to manual mode and turns teleop on, which is what an axis
//! jog needs.
//!
//! # Safety Warning
//!
//! This program moves the machine! Ensure you have clear access to E-stop
//! and understand the jog parameters before running.

use clap::Parser;
use futures_util::FutureExt;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{
    linux_cnc_command, CommandResponse, GetStatusRequest, InterpState, JogCommand, JogType,
    LinuxCncCommand, LinuxCncStatus, ModeCommand, RcsStatus, TaskMode, TaskState, TeleopCommand,
    TrajMode, WaitCompleteRequest,
};
use std::io::Write;
use std::panic::{resume_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tonic::transport::{Channel, Endpoint};

// Deadlines. Every RPC gets one.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const RPC_TIMEOUT: Duration = Duration::from_secs(5); // GetStatus and SendCommand
const JOG_STOP_TIMEOUT: Duration = Duration::from_secs(2); // The jog stop has its own short deadline
const STATE_CHANGE_TIMEOUT: Duration = Duration::from_secs(5); // A mode or teleop change must finish within this
const WAIT_DEADLINE_MARGIN: Duration = Duration::from_secs(5); // WaitComplete's client deadline = its timeout + this
const MOTION_START_TIMEOUT: Duration = Duration::from_secs(2); // A jog that has not moved by now was refused
const MOTION_TIMEOUT: Duration = Duration::from_secs(10); // A jog must be finished and stopped within this
const POLL_INTERVAL: Duration = Duration::from_millis(50);

// The demo moves. Jog velocity is in machine units per second.
const AXIS_LETTERS: [char; 9] = ['X', 'Y', 'Z', 'A', 'B', 'C', 'U', 'V', 'W'];
const INCREMENT_AXIS: usize = 0; // X
const CONTINUOUS_AXIS: usize = 1; // Y
const JOG_VELOCITY: f64 = 1.0; // Machine units per second
const INCREMENT_DISTANCE: f64 = 1.0; // Machine units
const CONTINUOUS_JOG_SECONDS: f64 = 0.5;
const MOVED_THRESHOLD: f64 = 0.01; // Machine units; more than feedback jitter, far less than either jog
const STOPPED_VELOCITY: f64 = 1e-6;

type ExampleResult<T> = Result<T, Box<dyn std::error::Error>>;

/// Print a line to standard output, ignoring a failed write.
///
/// `println!` panics when the write fails, for example when the reader of a
/// pipe has closed it. That is fine before anything has moved, but once the
/// first jog has started, losing the output must not end the program before
/// a stop is sent: from there on everything is printed with `say!` or
/// `say_error!`.
macro_rules! say {
    ($($argument:tt)*) => {{
        let _ = writeln!(std::io::stdout(), $($argument)*);
    }};
}

/// Like `say!`, for standard error.
macro_rules! say_error {
    ($($argument:tt)*) => {{
        let _ = writeln!(std::io::stderr(), $($argument)*);
    }};
}

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

/// Ctrl+C or SIGTERM arrived.
#[derive(Debug)]
struct Interrupted;

impl std::fmt::Display for Interrupted {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "interrupted")
    }
}

impl std::error::Error for Interrupted {}

/// Set by Ctrl+C or SIGTERM. The signal task only sets the flag; the waits
/// check it, so the program always leaves through the code that stops the jog.
struct InterruptFlag(Arc<AtomicBool>);

impl InterruptFlag {
    /// Start listening for Ctrl+C (and SIGTERM on Unix).
    fn install() -> std::io::Result<Self> {
        let was_interrupted = Arc::new(AtomicBool::new(false));
        let flag_for_signal_task = was_interrupted.clone();

        #[cfg(unix)]
        {
            use tokio::signal::unix::{signal, SignalKind};
            // Creating these registers the handlers right here, before
            // anything moves.
            let mut interrupt_signal = signal(SignalKind::interrupt())?;
            let mut terminate_signal = signal(SignalKind::terminate())?;
            tokio::spawn(async move {
                tokio::select! {
                    _ = interrupt_signal.recv() => {}
                    _ = terminate_signal.recv() => {}
                }
                flag_for_signal_task.store(true, Ordering::SeqCst);
            });
        }

        #[cfg(not(unix))]
        tokio::spawn(async move {
            let _ = tokio::signal::ctrl_c().await;
            flag_for_signal_task.store(true, Ordering::SeqCst);
        });

        Ok(Self(was_interrupted))
    }

    /// Sleep, but return `Interrupted` as soon as an interrupt arrives.
    async fn pause(&self, duration: Duration) -> ExampleResult<()> {
        let wake_time = Instant::now() + duration;
        loop {
            if self.0.load(Ordering::SeqCst) {
                return Err(Interrupted.into());
            }
            let remaining = wake_time.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Ok(());
            }
            tokio::time::sleep(remaining.min(Duration::from_millis(20))).await;
        }
    }
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
        deadline: Duration,
    ) -> ExampleResult<CommandResponse> {
        let command_message = LinuxCncCommand {
            serial: self.next_serial(),
            timestamp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos() as i64,
            command: Some(command),
            ..Default::default()
        };
        let request = with_deadline(command_message, deadline);
        Ok(self.client.send_command(request).await?.into_inner())
    }

    /// Wait until the command that `sent` answers has finished.
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
            // Set: wait on this command's serial.
            linuxcnc_serial: sent.linuxcnc_serial,
            ..Default::default()
        };
        // The client deadline must be longer than the wait asked of the server.
        let request = with_deadline(wait_request, timeout + WAIT_DEADLINE_MARGIN);
        Ok(self.client.wait_complete(request).await?.into_inner())
    }

    async fn set_mode(&mut self, mode: TaskMode) -> ExampleResult<CommandResponse> {
        let command = linux_cnc_command::Command::Mode(ModeCommand { mode: mode as i32 });
        self.send_command(command, RPC_TIMEOUT).await
    }

    /// Turn teleop on (axis jogs) or off (joint jogs, homing).
    async fn set_teleop(&mut self, enable: bool) -> ExampleResult<CommandResponse> {
        let command = linux_cnc_command::Command::Teleop(TeleopCommand { enable });
        self.send_command(command, RPC_TIMEOUT).await
    }

    /// Start a continuous axis jog. velocity > 0 for positive, < 0 for negative.
    async fn jog_continuous(&mut self, axis: usize, velocity: f64) -> ExampleResult<CommandResponse> {
        let command = linux_cnc_command::Command::Jog(JogCommand {
            r#type: JogType::JogContinuous as i32,
            is_joint: false,    // Axis jog: needs teleop on
            index: axis as i32, // 0=X, 1=Y, 2=Z, etc.
            velocity,
            ..Default::default()
        });
        self.send_command(command, RPC_TIMEOUT).await
    }

    /// Jog an axis by a fixed distance.
    async fn jog_increment(
        &mut self,
        axis: usize,
        velocity: f64,
        distance: f64,
    ) -> ExampleResult<CommandResponse> {
        let command = linux_cnc_command::Command::Jog(JogCommand {
            r#type: JogType::JogIncrement as i32,
            is_joint: false,
            index: axis as i32,
            velocity,
            increment: distance,
        });
        self.send_command(command, RPC_TIMEOUT).await
    }

    /// Stop jogging an axis. Uses its own short deadline.
    async fn jog_stop(&mut self, axis: usize) -> ExampleResult<CommandResponse> {
        let command = linux_cnc_command::Command::Jog(JogCommand {
            r#type: JogType::JogStop as i32,
            is_joint: false,
            index: axis as i32,
            ..Default::default()
        });
        self.send_command(command, JOG_STOP_TIMEOUT).await
    }
}

fn was_dispatched(response: &CommandResponse) -> bool {
    response.status == RcsStatus::RcsDone as i32
}

fn axis_position(status: &LinuxCncStatus, axis: usize) -> ExampleResult<f64> {
    let position = status
        .position
        .as_ref()
        .and_then(|position_status| position_status.actual_position.as_ref())
        .ok_or("the status has no position")?;
    Ok([
        position.x, position.y, position.z, position.a, position.b, position.c, position.u,
        position.v, position.w,
    ][axis])
}

/// The newest messages the server has read from LinuxCNC, if it reads them.
fn recent_operator_messages(status: &LinuxCncStatus) -> String {
    if status.errors.is_empty() {
        return "Look at the operator messages in the LinuxCNC GUI for the reason.".to_string();
    }
    let newest_messages: Vec<&str> = status
        .errors
        .iter()
        .rev()
        .take(3)
        .rev()
        .map(|error| error.message.as_str())
        .collect();
    format!("Recent LinuxCNC messages: {}", newest_messages.join("; "))
}

/// Wait for a mode or teleop command to finish, then check the status shows it.
async fn change_and_confirm(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
    sent: CommandResponse,
    description: &str,
    is_confirmed: impl Fn(&LinuxCncStatus) -> bool,
) -> ExampleResult<()> {
    if !was_dispatched(&sent) {
        return Err(format!("{description} was refused: {}", sent.error_message).into());
    }

    let finished = client.wait_complete(&sent, STATE_CHANGE_TIMEOUT).await?;
    if finished.status == RcsStatus::RcsExec as i32 {
        return Err(format!("{description} had not finished after {STATE_CHANGE_TIMEOUT:?}").into());
    }
    if finished.status != RcsStatus::RcsDone as i32 {
        return Err(format!("{description} failed: {}", finished.error_message).into());
    }

    // Finished is not the same as in effect: check the status, with a deadline.
    let deadline = Instant::now() + STATE_CHANGE_TIMEOUT;
    while !is_confirmed(&client.get_status().await?) {
        if Instant::now() >= deadline {
            return Err(format!(
                "{description} did not show in the status within {STATE_CHANGE_TIMEOUT:?}"
            )
            .into());
        }
        interrupt.pause(POLL_INTERVAL).await?;
    }
    Ok(())
}

fn task_mode(status: &LinuxCncStatus) -> Option<i32> {
    status.task.as_ref().map(|task| task.task_mode)
}

fn motion_mode(status: &LinuxCncStatus) -> Option<i32> {
    status.trajectory.as_ref().map(|trajectory| trajectory.motion_mode)
}

/// Check the machine is on, homed and idle, then set manual mode and teleop.
async fn prepare_for_axis_jog(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
) -> ExampleResult<()> {
    let mut status = client.get_status().await?;
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
    // An axis jog needs teleop, and teleop needs every joint homed.
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

    for axis in [INCREMENT_AXIS, CONTINUOUS_AXIS] {
        if trajectory.axis_mask & (1 << axis) == 0 {
            return Err(format!("This machine has no {} axis.", AXIS_LETTERS[axis]).into());
        }
    }

    // Checked whatever the mode is: never jog, or change mode, under a
    // running program or MDI command.
    if task.interp_state != InterpState::InterpIdle as i32 {
        let interp_state_name = InterpState::try_from(task.interp_state)
            .map(|state| state.as_str_name())
            .unwrap_or("unknown");
        return Err(format!(
            "The interpreter is still busy with a program or MDI command (state \
             {interp_state_name}). Nothing was moved; wait for it to finish and run this again."
        )
        .into());
    }

    if task.task_mode != TaskMode::ModeManual as i32 {
        println!("Setting manual mode...");
        let sent = client.set_mode(TaskMode::ModeManual).await?;
        change_and_confirm(client, interrupt, sent, "Manual mode", |new_status| {
            task_mode(new_status) == Some(TaskMode::ModeManual as i32)
        })
        .await?;
        status = client.get_status().await?;
    }

    if motion_mode(&status) != Some(TrajMode::Teleop as i32) {
        println!("Turning teleop on...");
        let sent = client.set_teleop(true).await?;
        change_and_confirm(client, interrupt, sent, "Teleop", |new_status| {
            motion_mode(new_status) == Some(TrajMode::Teleop as i32)
        })
        .await?;
    }

    Ok(())
}

/// Send the jog stop and insist that it was dispatched.
async fn stop_jog(client: &mut LinuxCncClient, axis: usize) -> ExampleResult<()> {
    let axis_letter = AXIS_LETTERS[axis];
    match client.jog_stop(axis).await {
        Ok(sent) if was_dispatched(&sent) => Ok(()),
        Ok(sent) => Err(format!(
            "The jog stop for {axis_letter} was refused: {}. The axis may still be moving: \
             use the E-stop.",
            sent.error_message
        )
        .into()),
        Err(error) => Err(format!(
            "The jog stop for {axis_letter} did not get through ({error}). The axis may still \
             be moving: use the E-stop."
        )
        .into()),
    }
}

/// Wait until the axis has moved and stopped again; return its position.
///
/// A jog that LinuxCNC refuses still answers `RcsDone`, so the only proof of a
/// jog is the status. `trajectory.inpos` stays true during a jog; velocity is
/// what shows motion.
async fn wait_for_motion_to_end(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
    axis: usize,
    start_position: f64,
) -> ExampleResult<f64> {
    let axis_letter = AXIS_LETTERS[axis];
    let started_waiting = Instant::now();
    loop {
        let status = client.get_status().await?;
        let position = axis_position(&status, axis)?;
        let current_velocity = status
            .trajectory
            .as_ref()
            .map_or(0.0, |trajectory| trajectory.current_vel);
        let has_moved = (position - start_position).abs() > MOVED_THRESHOLD;
        let is_stopped = current_velocity.abs() < STOPPED_VELOCITY;
        if has_moved && is_stopped {
            return Ok(position);
        }

        let waited = started_waiting.elapsed();
        if !has_moved && waited >= MOTION_START_TIMEOUT {
            return Err(format!(
                "{axis_letter} did not move, so LinuxCNC refused the jog. {}",
                recent_operator_messages(&status)
            )
            .into());
        }
        if waited >= MOTION_TIMEOUT {
            return Err(format!("{axis_letter} was still moving after {MOTION_TIMEOUT:?}").into());
        }
        interrupt.pause(POLL_INTERVAL).await?;
    }
}

/// Send the incremental jog and wait until the axis has arrived.
async fn send_increment_and_wait(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
    start_position: f64,
) -> ExampleResult<f64> {
    let sent = client
        .jog_increment(INCREMENT_AXIS, JOG_VELOCITY, INCREMENT_DISTANCE)
        .await?;
    if !was_dispatched(&sent) {
        return Err(format!("Incremental jog was refused: {}", sent.error_message).into());
    }
    wait_for_motion_to_end(client, interrupt, INCREMENT_AXIS, start_position).await
}

/// Jog one axis by a fixed distance and wait until it has arrived.
async fn demo_incremental_jog(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
) -> ExampleResult<()> {
    let axis_letter = AXIS_LETTERS[INCREMENT_AXIS];
    println!("\n--- Incremental Jog Demo ---");
    println!("Jogging {axis_letter} by +{INCREMENT_DISTANCE} at {JOG_VELOCITY} units/s...");

    let start_position = axis_position(&client.get_status().await?, INCREMENT_AXIS)?;
    interrupt.pause(Duration::ZERO).await?; // Do not start a jog after an interrupt

    // The jog is run so that neither an error nor a panic in it can get past
    // the stop below. Nothing is printed between the start and the stop.
    let jog_outcome = AssertUnwindSafe(send_increment_and_wait(client, interrupt, start_position))
        .catch_unwind()
        .await;
    match jog_outcome {
        Ok(Ok(end_position)) => {
            // No stop needed: the status showed the axis moved and is
            // standing still.
            say!("{axis_letter} moved from {start_position:.4} to {end_position:.4}");
            Ok(())
        }
        Ok(Err(jog_error)) => {
            // Interrupted or failed part way (including a start whose answer
            // never came): do not leave the axis moving.
            stop_jog(client, INCREMENT_AXIS).await?;
            Err(jog_error)
        }
        Err(panic_payload) => {
            // The resumed panic would lose a failed stop: report it first.
            if let Err(stop_error) = stop_jog(client, INCREMENT_AXIS).await {
                say_error!("Error: {stop_error}");
            }
            resume_unwind(panic_payload)
        }
    }
}

/// Start the continuous jog and keep it going for the demo time. The caller
/// always sends the stop afterwards.
async fn start_and_hold_continuous_jog(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
) -> ExampleResult<()> {
    let sent = client.jog_continuous(CONTINUOUS_AXIS, JOG_VELOCITY).await?;
    if !was_dispatched(&sent) {
        return Err(format!("Continuous jog was refused: {}", sent.error_message).into());
    }
    interrupt
        .pause(Duration::from_secs_f64(CONTINUOUS_JOG_SECONDS))
        .await
}

/// Jog one axis continuously for a moment, then stop it.
async fn demo_continuous_jog(
    client: &mut LinuxCncClient,
    interrupt: &InterruptFlag,
) -> ExampleResult<()> {
    let axis_letter = AXIS_LETTERS[CONTINUOUS_AXIS];
    say!("\n--- Continuous Jog Demo ---");
    say!(
        "Jogging {axis_letter} at +{JOG_VELOCITY} units/s for {CONTINUOUS_JOG_SECONDS} seconds..."
    );

    let start_position = axis_position(&client.get_status().await?, CONTINUOUS_AXIS)?;
    interrupt.pause(Duration::ZERO).await?; // Do not start a jog after an interrupt

    // The stop is always sent: after the sleep, on Ctrl+C, on any error
    // (including a start whose answer never came) and on a panic. A `?` on
    // the jog itself would return past the stop, and a panic would unwind
    // past it, so the outcome is caught, kept and looked at after the stop.
    // Nothing is printed between the start and the stop.
    let jog_outcome = AssertUnwindSafe(start_and_hold_continuous_jog(client, interrupt))
        .catch_unwind()
        .await;
    let stop_result = stop_jog(client, CONTINUOUS_AXIS).await;
    match jog_outcome {
        Ok(jog_result) => {
            stop_result?;
            jog_result?;
        }
        Err(panic_payload) => {
            // The resumed panic would lose a failed stop: report it first.
            if let Err(stop_error) = stop_result {
                say_error!("Error: {stop_error}");
            }
            resume_unwind(panic_payload)
        }
    }
    say!("Jog stop sent.");

    let end_position =
        wait_for_motion_to_end(client, interrupt, CONTINUOUS_AXIS, start_position).await?;
    say!("{axis_letter} moved from {start_position:.4} to {end_position:.4}");
    Ok(())
}

async fn run(args: Args) -> ExampleResult<()> {
    // Installed before anything moves.
    let interrupt = InterruptFlag::install()?;

    let addr = format!("http://{}:{}", args.host, args.port);
    let mut client = LinuxCncClient::connect(&addr).await?;

    // Show initial status
    let status = client.get_status().await?;
    if let Some(position) = status
        .position
        .as_ref()
        .and_then(|position_status| position_status.actual_position.as_ref())
    {
        println!(
            "Current position: X={:.4} Y={:.4} Z={:.4}",
            position.x, position.y, position.z
        );
    }

    if args.skip_demo {
        println!("Skipping demo movements (--skip-demo)");
        return Ok(());
    }

    prepare_for_axis_jog(&mut client, &interrupt).await?;
    demo_incremental_jog(&mut client, &interrupt).await?;
    demo_continuous_jog(&mut client, &interrupt).await?;

    say!("\nJog demo complete!");
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(error) = run(Args::parse()).await {
        say_error!("Error: {error}");
        let exit_code = if error.is::<Interrupted>() { 130 } else { 1 };
        std::process::exit(exit_code);
    }
}
