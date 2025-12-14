//! Integration tests for LinuxCNC gRPC Rust client.
//!
//! These tests connect to a Python mock server for cross-language validation.

use std::process::{Child, Command, Stdio};
use std::io::{BufRead, BufReader};
use std::time::Duration;

use linuxcnc_grpc::linuxcnc::{
    linux_cnc_service_client::LinuxCncServiceClient,
    GetStatusRequest, LinuxCncCommand, WaitCompleteRequest, StreamStatusRequest,
    StateCommand, MdiCommand, JogCommand,
    TaskMode, TaskState, ExecState, RcsStatus, JogType,
};
use linuxcnc_grpc::hal::{
    hal_service_client::HalServiceClient,
    GetSystemStatusRequest, QueryPinsCommand, QuerySignalsCommand,
    QueryParamsCommand, QueryComponentsCommand, GetValueCommand,
    HalStreamStatusRequest, HalType,
};
use tokio_stream::StreamExt;

const MOCK_SERVER_PORT: u16 = 50096;

struct MockServer {
    process: Child,
}

impl MockServer {
    fn start() -> Result<Self, Box<dyn std::error::Error>> {
        let mut process = Command::new("python3")
            .args(["../../tests/mock_server.py", "--port", &MOCK_SERVER_PORT.to_string()])
            .current_dir(env!("CARGO_MANIFEST_DIR"))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        // Wait for ready signal
        let stdout = process.stdout.take().unwrap();
        let reader = BufReader::new(stdout);

        for line in reader.lines() {
            let line = line?;
            if line.starts_with("READY:") {
                // Put stdout back (even though we've consumed it)
                break;
            }
        }

        Ok(MockServer { process })
    }

    fn address(&self) -> String {
        format!("http://localhost:{}", MOCK_SERVER_PORT)
    }
}

impl Drop for MockServer {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
    }
}

// =============================================================================
// LinuxCNC Service Tests
// =============================================================================

#[tokio::test]
async fn test_get_status() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = LinuxCncServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .get_status(GetStatusRequest {})
        .await
        .expect("GetStatus failed");
    let status = response.into_inner();

    // Verify task status
    assert_eq!(status.task.as_ref().unwrap().task_mode, TaskMode::ModeManual as i32);
    assert_eq!(status.task.as_ref().unwrap().task_state, TaskState::StateOn as i32);
    assert_eq!(status.task.as_ref().unwrap().exec_state, ExecState::ExecDone as i32);
    assert_eq!(status.task.as_ref().unwrap().echo_serial_number, 1234);

    // Verify trajectory
    let trajectory = status.trajectory.as_ref().unwrap();
    assert_eq!(trajectory.joints, 3);
    assert!(trajectory.enabled);

    // Verify position
    let position = status.position.as_ref().unwrap();
    let actual = position.actual_position.as_ref().unwrap();
    assert_eq!(actual.x, 1.0);
    assert_eq!(actual.y, 2.0);
    assert_eq!(actual.z, 3.0);

    // Verify joints
    assert_eq!(status.joints.len(), 3);
    for joint in &status.joints {
        assert!(joint.homed);
        assert!(joint.enabled);
    }

    // Verify tool
    assert_eq!(status.tool.as_ref().unwrap().tool_in_spindle, 1);
}

#[tokio::test]
async fn test_send_command_state() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = LinuxCncServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let command = LinuxCncCommand {
        serial: 100,
        command: Some(linuxcnc_grpc::linuxcnc::linux_cnc_command::Command::State(
            StateCommand {
                state: TaskState::StateOn as i32,
            },
        )),
    };

    let response = client
        .send_command(command)
        .await
        .expect("SendCommand failed");
    let resp = response.into_inner();

    assert_eq!(resp.serial, 100);
    assert_eq!(resp.status, RcsStatus::RcsDone as i32);
}

#[tokio::test]
async fn test_send_command_mdi() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = LinuxCncServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let command = LinuxCncCommand {
        serial: 101,
        command: Some(linuxcnc_grpc::linuxcnc::linux_cnc_command::Command::Mdi(
            MdiCommand {
                command: "G0 X10 Y10".to_string(),
            },
        )),
    };

    let response = client
        .send_command(command)
        .await
        .expect("SendCommand failed");
    let resp = response.into_inner();

    assert_eq!(resp.serial, 101);
    assert_eq!(resp.status, RcsStatus::RcsDone as i32);
}

#[tokio::test]
async fn test_send_command_jog() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = LinuxCncServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let command = LinuxCncCommand {
        serial: 102,
        command: Some(linuxcnc_grpc::linuxcnc::linux_cnc_command::Command::Jog(
            JogCommand {
                r#type: JogType::JogContinuous as i32,
                is_joint: false,
                index: 0,
                velocity: 10.0,
                distance: 0.0,
            },
        )),
    };

    let response = client
        .send_command(command)
        .await
        .expect("SendCommand failed");
    let resp = response.into_inner();

    assert_eq!(resp.serial, 102);
    assert_eq!(resp.status, RcsStatus::RcsDone as i32);
}

#[tokio::test]
async fn test_wait_complete() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = LinuxCncServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .wait_complete(WaitCompleteRequest {
            serial: 50,
            timeout: 5.0,
        })
        .await
        .expect("WaitComplete failed");
    let resp = response.into_inner();

    assert_eq!(resp.serial, 50);
    assert_eq!(resp.status, RcsStatus::RcsDone as i32);
}

#[tokio::test]
async fn test_stream_status() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = LinuxCncServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let mut stream = client
        .stream_status(StreamStatusRequest { interval: 0.05 })
        .await
        .expect("StreamStatus failed")
        .into_inner();

    let mut count = 0;
    while let Some(result) = stream.next().await {
        let status = result.expect("Stream error");
        assert_eq!(
            status.task.as_ref().unwrap().task_mode,
            TaskMode::ModeManual as i32
        );
        count += 1;
        if count >= 3 {
            break;
        }
    }

    assert_eq!(count, 3);
}

// =============================================================================
// HAL Service Tests
// =============================================================================

#[tokio::test]
async fn test_hal_get_system_status() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .get_system_status(GetSystemStatusRequest {})
        .await
        .expect("GetSystemStatus failed");
    let status = response.into_inner();

    // Verify system info
    assert!(status.is_sim);
    assert!(status.is_userspace);
    assert_eq!(status.kernel_version, "mock");

    // Verify pins
    assert_eq!(status.pins.len(), 3);

    // Verify signals
    assert_eq!(status.signals.len(), 2);

    // Verify components
    assert_eq!(status.components.len(), 3);
}

#[tokio::test]
async fn test_hal_query_pins() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    // Query all pins
    let response = client
        .query_pins(QueryPinsCommand {
            serial: 0,
            pattern: "*".to_string(),
        })
        .await
        .expect("QueryPins failed");
    let resp = response.into_inner();

    assert!(resp.success);
    assert_eq!(resp.pins.len(), 3);

    // Query filtered
    let response = client
        .query_pins(QueryPinsCommand {
            serial: 0,
            pattern: "axis.*".to_string(),
        })
        .await
        .expect("QueryPins failed");
    let resp = response.into_inner();

    assert!(resp.success);
    assert_eq!(resp.pins.len(), 1);
    assert_eq!(resp.pins[0].name, "axis.x.pos-cmd");
}

#[tokio::test]
async fn test_hal_query_signals() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .query_signals(QuerySignalsCommand {
            serial: 0,
            pattern: "*".to_string(),
        })
        .await
        .expect("QuerySignals failed");
    let resp = response.into_inner();

    assert!(resp.success);
    assert_eq!(resp.signals.len(), 2);
}

#[tokio::test]
async fn test_hal_query_params() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .query_params(QueryParamsCommand {
            serial: 0,
            pattern: "*".to_string(),
        })
        .await
        .expect("QueryParams failed");
    let resp = response.into_inner();

    assert!(resp.success);
    assert_eq!(resp.params.len(), 3);
}

#[tokio::test]
async fn test_hal_query_components() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .query_components(QueryComponentsCommand {
            serial: 0,
            pattern: "*".to_string(),
        })
        .await
        .expect("QueryComponents failed");
    let resp = response.into_inner();

    assert!(resp.success);
    assert_eq!(resp.components.len(), 3);

    let names: Vec<&str> = resp.components.iter().map(|c| c.name.as_str()).collect();
    assert!(names.contains(&"axis"));
    assert!(names.contains(&"spindle"));
    assert!(names.contains(&"iocontrol"));
}

#[tokio::test]
async fn test_hal_get_value() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let response = client
        .get_value(GetValueCommand {
            serial: 0,
            name: "axis.x.pos-cmd".to_string(),
        })
        .await
        .expect("GetValue failed");
    let resp = response.into_inner();

    assert!(resp.success);
    assert_eq!(resp.r#type, HalType::HalFloat as i32);
    assert_eq!(resp.value.as_ref().unwrap().float_value, 123.456);
}

#[tokio::test]
async fn test_hal_stream_status() {
    let server = MockServer::start().expect("Failed to start mock server");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut client = HalServiceClient::connect(server.address())
        .await
        .expect("Failed to connect");

    let mut stream = client
        .stream_status(HalStreamStatusRequest {
            interval: 0.05,
            filter: vec![],
        })
        .await
        .expect("StreamStatus failed")
        .into_inner();

    let mut count = 0;
    while let Some(result) = stream.next().await {
        let status = result.expect("Stream error");
        assert!(status.is_sim);
        count += 1;
        if count >= 3 {
            break;
        }
    }

    assert_eq!(count, 3);
}
