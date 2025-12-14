// Package linuxcnc provides integration tests for the LinuxCNC gRPC client.
// These tests connect to a Python mock server for cross-language validation.
package linuxcnc

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// TestFixtures holds expected values loaded from JSON fixtures.
type TestFixtures struct {
	LinuxCNCStatus map[string]interface{}
	HalStatus      map[string]interface{}
}

var (
	mockServer *exec.Cmd
	serverAddr = "localhost:50098"
	fixtures   TestFixtures
)

func TestMain(m *testing.M) {
	// Load fixtures
	if err := loadFixtures(); err != nil {
		panic("failed to load fixtures: " + err.Error())
	}

	// Start mock server
	if err := startMockServer(); err != nil {
		panic("failed to start mock server: " + err.Error())
	}

	// Run tests
	code := m.Run()

	// Cleanup
	stopMockServer()

	os.Exit(code)
}

func loadFixtures() error {
	// Get path relative to test file
	basePath := "tests/fixtures"

	data, err := os.ReadFile(filepath.Join(basePath, "linuxcnc_status.json"))
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, &fixtures.LinuxCNCStatus); err != nil {
		return err
	}

	data, err = os.ReadFile(filepath.Join(basePath, "hal_status.json"))
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, &fixtures.HalStatus); err != nil {
		return err
	}

	return nil
}

func startMockServer() error {
	mockServer = exec.Command("python3", "tests/mock_server.py", "--port", "50098")
	mockServer.Stderr = os.Stderr

	stdout, err := mockServer.StdoutPipe()
	if err != nil {
		return err
	}

	if err := mockServer.Start(); err != nil {
		return err
	}

	// Wait for ready signal
	scanner := bufio.NewScanner(stdout)
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) && scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "READY:") {
			return nil
		}
	}

	mockServer.Process.Kill()
	return exec.ErrNotFound
}

func stopMockServer() {
	if mockServer != nil && mockServer.Process != nil {
		mockServer.Process.Kill()
		mockServer.Wait()
	}
}

func newLinuxCNCClient(t *testing.T) (LinuxCNCServiceClient, *grpc.ClientConn) {
	conn, err := grpc.NewClient(serverAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("failed to connect: %v", err)
	}
	return NewLinuxCNCServiceClient(conn), conn
}

func newHalClient(t *testing.T) (HalServiceClient, *grpc.ClientConn) {
	conn, err := grpc.NewClient(serverAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("failed to connect: %v", err)
	}
	return NewHalServiceClient(conn), conn
}

// =============================================================================
// LinuxCNC Service Tests
// =============================================================================

func TestGetStatus(t *testing.T) {
	client, conn := newLinuxCNCClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	status, err := client.GetStatus(ctx, &GetStatusRequest{})
	if err != nil {
		t.Fatalf("GetStatus failed: %v", err)
	}

	// Verify task status
	if status.Task.TaskMode != TaskMode_MODE_MANUAL {
		t.Errorf("expected MODE_MANUAL, got %v", status.Task.TaskMode)
	}
	if status.Task.TaskState != TaskState_STATE_ON {
		t.Errorf("expected STATE_ON, got %v", status.Task.TaskState)
	}
	if status.Task.ExecState != ExecState_EXEC_DONE {
		t.Errorf("expected EXEC_DONE, got %v", status.Task.ExecState)
	}

	// Verify trajectory
	if status.Trajectory.Joints != 3 {
		t.Errorf("expected 3 joints, got %d", status.Trajectory.Joints)
	}
	if !status.Trajectory.Enabled {
		t.Error("expected trajectory enabled")
	}

	// Verify position
	if status.Position.ActualPosition.X != 1.0 {
		t.Errorf("expected X=1.0, got %f", status.Position.ActualPosition.X)
	}
	if status.Position.ActualPosition.Y != 2.0 {
		t.Errorf("expected Y=2.0, got %f", status.Position.ActualPosition.Y)
	}
	if status.Position.ActualPosition.Z != 3.0 {
		t.Errorf("expected Z=3.0, got %f", status.Position.ActualPosition.Z)
	}

	// Verify joints
	if len(status.Joints) != 3 {
		t.Fatalf("expected 3 joints, got %d", len(status.Joints))
	}
	for i, joint := range status.Joints {
		if !joint.Homed {
			t.Errorf("joint %d expected homed", i)
		}
		if !joint.Enabled {
			t.Errorf("joint %d expected enabled", i)
		}
	}

	// Verify tool
	if status.Tool.ToolInSpindle != 1 {
		t.Errorf("expected tool 1 in spindle, got %d", status.Tool.ToolInSpindle)
	}
}

func TestSendCommandState(t *testing.T) {
	client, conn := newLinuxCNCClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.SendCommand(ctx, &LinuxCNCCommand{
		Serial: 100,
		Command: &LinuxCNCCommand_State{
			State: &StateCommand{State: TaskState_STATE_ON},
		},
	})
	if err != nil {
		t.Fatalf("SendCommand failed: %v", err)
	}

	if resp.Serial != 100 {
		t.Errorf("expected serial 100, got %d", resp.Serial)
	}
	if resp.Status != RcsStatus_RCS_DONE {
		t.Errorf("expected RCS_DONE, got %v", resp.Status)
	}
}

func TestSendCommandMdi(t *testing.T) {
	client, conn := newLinuxCNCClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.SendCommand(ctx, &LinuxCNCCommand{
		Serial: 101,
		Command: &LinuxCNCCommand_Mdi{
			Mdi: &MdiCommand{Command: "G0 X10 Y10"},
		},
	})
	if err != nil {
		t.Fatalf("SendCommand failed: %v", err)
	}

	if resp.Serial != 101 {
		t.Errorf("expected serial 101, got %d", resp.Serial)
	}
	if resp.Status != RcsStatus_RCS_DONE {
		t.Errorf("expected RCS_DONE, got %v", resp.Status)
	}
}

func TestSendCommandJog(t *testing.T) {
	client, conn := newLinuxCNCClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.SendCommand(ctx, &LinuxCNCCommand{
		Serial: 102,
		Command: &LinuxCNCCommand_Jog{
			Jog: &JogCommand{
				Type:     JogType_JOG_CONTINUOUS,
				IsJoint:  false,
				Index:    0,
				Velocity: 10.0,
			},
		},
	})
	if err != nil {
		t.Fatalf("SendCommand failed: %v", err)
	}

	if resp.Serial != 102 {
		t.Errorf("expected serial 102, got %d", resp.Serial)
	}
}

func TestWaitComplete(t *testing.T) {
	client, conn := newLinuxCNCClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.WaitComplete(ctx, &WaitCompleteRequest{
		Serial:  50,
		Timeout: 5.0,
	})
	if err != nil {
		t.Fatalf("WaitComplete failed: %v", err)
	}

	if resp.Serial != 50 {
		t.Errorf("expected serial 50, got %d", resp.Serial)
	}
	if resp.Status != RcsStatus_RCS_DONE {
		t.Errorf("expected RCS_DONE, got %v", resp.Status)
	}
}

func TestStreamStatus(t *testing.T) {
	client, conn := newLinuxCNCClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, err := client.StreamStatus(ctx, &StreamStatusRequest{Interval: 0.05})
	if err != nil {
		t.Fatalf("StreamStatus failed: %v", err)
	}

	// Get a few updates
	count := 0
	for count < 3 {
		status, err := stream.Recv()
		if err != nil {
			t.Fatalf("Recv failed: %v", err)
		}
		if status.Task.TaskMode != TaskMode_MODE_MANUAL {
			t.Errorf("expected MODE_MANUAL in stream, got %v", status.Task.TaskMode)
		}
		count++
	}

	if count != 3 {
		t.Errorf("expected 3 updates, got %d", count)
	}
}

// =============================================================================
// HAL Service Tests
// =============================================================================

func TestHalGetSystemStatus(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	status, err := client.GetSystemStatus(ctx, &GetSystemStatusRequest{})
	if err != nil {
		t.Fatalf("GetSystemStatus failed: %v", err)
	}

	// Verify system info
	if !status.IsSim {
		t.Error("expected is_sim = true")
	}
	if !status.IsUserspace {
		t.Error("expected is_userspace = true")
	}
	if status.KernelVersion != "mock" {
		t.Errorf("expected kernel_version 'mock', got '%s'", status.KernelVersion)
	}

	// Verify pins
	if len(status.Pins) != 3 {
		t.Errorf("expected 3 pins, got %d", len(status.Pins))
	}

	// Verify signals
	if len(status.Signals) != 2 {
		t.Errorf("expected 2 signals, got %d", len(status.Signals))
	}

	// Verify components
	if len(status.Components) != 3 {
		t.Errorf("expected 3 components, got %d", len(status.Components))
	}
}

func TestHalQueryPins(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Query all pins
	resp, err := client.QueryPins(ctx, &QueryPinsCommand{Pattern: "*"})
	if err != nil {
		t.Fatalf("QueryPins failed: %v", err)
	}

	if !resp.Success {
		t.Errorf("QueryPins failed: %s", resp.Error)
	}
	if len(resp.Pins) != 3 {
		t.Errorf("expected 3 pins, got %d", len(resp.Pins))
	}

	// Query filtered
	resp, err = client.QueryPins(ctx, &QueryPinsCommand{Pattern: "axis.*"})
	if err != nil {
		t.Fatalf("QueryPins failed: %v", err)
	}

	if len(resp.Pins) != 1 {
		t.Errorf("expected 1 pin matching axis.*, got %d", len(resp.Pins))
	}
	if resp.Pins[0].Name != "axis.x.pos-cmd" {
		t.Errorf("expected axis.x.pos-cmd, got %s", resp.Pins[0].Name)
	}
}

func TestHalQuerySignals(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.QuerySignals(ctx, &QuerySignalsCommand{Pattern: "*"})
	if err != nil {
		t.Fatalf("QuerySignals failed: %v", err)
	}

	if !resp.Success {
		t.Errorf("QuerySignals failed: %s", resp.Error)
	}
	if len(resp.Signals) != 2 {
		t.Errorf("expected 2 signals, got %d", len(resp.Signals))
	}
}

func TestHalQueryParams(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.QueryParams(ctx, &QueryParamsCommand{Pattern: "*"})
	if err != nil {
		t.Fatalf("QueryParams failed: %v", err)
	}

	if !resp.Success {
		t.Errorf("QueryParams failed: %s", resp.Error)
	}
	if len(resp.Params) != 3 {
		t.Errorf("expected 3 params, got %d", len(resp.Params))
	}
}

func TestHalQueryComponents(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.QueryComponents(ctx, &QueryComponentsCommand{Pattern: "*"})
	if err != nil {
		t.Fatalf("QueryComponents failed: %v", err)
	}

	if !resp.Success {
		t.Errorf("QueryComponents failed: %s", resp.Error)
	}
	if len(resp.Components) != 3 {
		t.Errorf("expected 3 components, got %d", len(resp.Components))
	}

	// Check component names
	names := make(map[string]bool)
	for _, c := range resp.Components {
		names[c.Name] = true
	}
	expected := []string{"axis", "spindle", "iocontrol"}
	for _, name := range expected {
		if !names[name] {
			t.Errorf("missing component: %s", name)
		}
	}
}

func TestHalGetValue(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.GetValue(ctx, &GetValueCommand{Name: "axis.x.pos-cmd"})
	if err != nil {
		t.Fatalf("GetValue failed: %v", err)
	}

	if !resp.Success {
		t.Errorf("GetValue failed: %s", resp.Error)
	}
	if resp.Type != HalType_HAL_FLOAT {
		t.Errorf("expected HAL_FLOAT, got %v", resp.Type)
	}
	if resp.Value.GetFloatValue() != 123.456 {
		t.Errorf("expected 123.456, got %f", resp.Value.GetFloatValue())
	}
}

func TestHalStreamStatus(t *testing.T) {
	client, conn := newHalClient(t)
	defer conn.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, err := client.StreamStatus(ctx, &HalStreamStatusRequest{Interval: 0.05})
	if err != nil {
		t.Fatalf("StreamStatus failed: %v", err)
	}

	// Get a few updates
	count := 0
	for count < 3 {
		status, err := stream.Recv()
		if err != nil {
			t.Fatalf("Recv failed: %v", err)
		}
		if !status.IsSim {
			t.Error("expected is_sim = true in stream")
		}
		count++
	}

	if count != 3 {
		t.Errorf("expected 3 updates, got %d", count)
	}
}
