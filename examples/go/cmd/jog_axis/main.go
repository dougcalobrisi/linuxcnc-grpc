// Jog Axis Example
//
// Demonstrates jogging an axis using the LinuxCNC gRPC server.
// Supports both continuous jogging and incremental jogging.
//
// Usage:
//
//	go run jog_axis.go [--host HOST] [--port PORT] [--skip-demo]
//
// Safety Warning:
//
//	This script moves the machine! Ensure you have clear access to E-stop
//	and understand the jog parameters before running.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

// LinuxCNCClient wraps the gRPC client with helper methods
type LinuxCNCClient struct {
	conn   *grpc.ClientConn
	client pb.LinuxCNCServiceClient
	serial atomic.Int32
}

func NewLinuxCNCClient(host string, port int) (*LinuxCNCClient, error) {
	addr := fmt.Sprintf("%s:%d", host, port)
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &LinuxCNCClient{
		conn:   conn,
		client: pb.NewLinuxCNCServiceClient(conn),
	}, nil
}

func (c *LinuxCNCClient) Close() error {
	return c.conn.Close()
}

func (c *LinuxCNCClient) nextSerial() int32 {
	return c.serial.Add(1)
}

func (c *LinuxCNCClient) GetStatus(ctx context.Context) (*pb.LinuxCNCStatus, error) {
	return c.client.GetStatus(ctx, &pb.GetStatusRequest{})
}

func (c *LinuxCNCClient) SendCommand(ctx context.Context, cmd *pb.LinuxCNCCommand) (*pb.CommandResponse, error) {
	cmd.Serial = c.nextSerial()
	cmd.Timestamp = time.Now().UnixNano()
	return c.client.SendCommand(ctx, cmd)
}

func (c *LinuxCNCClient) SetMode(ctx context.Context, mode pb.TaskMode) (*pb.CommandResponse, error) {
	cmd := &pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Mode{
			Mode: &pb.ModeCommand{Mode: mode},
		},
	}
	return c.SendCommand(ctx, cmd)
}

func (c *LinuxCNCClient) SetState(ctx context.Context, state pb.TaskState) (*pb.CommandResponse, error) {
	cmd := &pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_State{
			State: &pb.StateCommand{State: state},
		},
	}
	return c.SendCommand(ctx, cmd)
}

func (c *LinuxCNCClient) JogContinuous(ctx context.Context, axis int32, velocity float64) (*pb.CommandResponse, error) {
	cmd := &pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Jog{
			Jog: &pb.JogCommand{
				Type:     pb.JogType_JOG_CONTINUOUS,
				IsJoint:  false,
				Index:    axis,
				Velocity: velocity,
			},
		},
	}
	return c.SendCommand(ctx, cmd)
}

func (c *LinuxCNCClient) JogIncrement(ctx context.Context, axis int32, velocity, increment float64) (*pb.CommandResponse, error) {
	cmd := &pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Jog{
			Jog: &pb.JogCommand{
				Type:      pb.JogType_JOG_INCREMENT,
				IsJoint:   false,
				Index:     axis,
				Velocity:  velocity,
				Increment: increment,
			},
		},
	}
	return c.SendCommand(ctx, cmd)
}

func (c *LinuxCNCClient) JogStop(ctx context.Context, axis int32) (*pb.CommandResponse, error) {
	cmd := &pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Jog{
			Jog: &pb.JogCommand{
				Type:     pb.JogType_JOG_STOP,
				IsJoint:  false,
				Index:    axis,
				Velocity: 0,
			},
		},
	}
	return c.SendCommand(ctx, cmd)
}

func ensureMachineReady(ctx context.Context, client *LinuxCNCClient) error {
	status, err := client.GetStatus(ctx)
	if err != nil {
		return err
	}

	// Check E-stop
	if status.Task.TaskState == pb.TaskState_STATE_ESTOP {
		fmt.Println("Machine is in E-stop. Resetting...")
		resp, err := client.SetState(ctx, pb.TaskState_STATE_ESTOP_RESET)
		if err != nil {
			return err
		}
		if resp.Status != pb.RcsStatus_RCS_DONE {
			return fmt.Errorf("failed to reset E-stop: %s", resp.ErrorMessage)
		}
		time.Sleep(100 * time.Millisecond)
		status, err = client.GetStatus(ctx)
		if err != nil {
			return err
		}
	}

	// Power on
	if status.Task.TaskState != pb.TaskState_STATE_ON {
		fmt.Println("Powering on machine...")
		resp, err := client.SetState(ctx, pb.TaskState_STATE_ON)
		if err != nil {
			return err
		}
		if resp.Status != pb.RcsStatus_RCS_DONE {
			return fmt.Errorf("failed to power on: %s", resp.ErrorMessage)
		}
		time.Sleep(100 * time.Millisecond)
		status, err = client.GetStatus(ctx)
		if err != nil {
			return err
		}
	}

	// Set manual mode for jogging
	if status.Task.TaskMode != pb.TaskMode_MODE_MANUAL {
		fmt.Println("Setting manual mode...")
		resp, err := client.SetMode(ctx, pb.TaskMode_MODE_MANUAL)
		if err != nil {
			return err
		}
		if resp.Status != pb.RcsStatus_RCS_DONE {
			return fmt.Errorf("failed to set manual mode: %s", resp.ErrorMessage)
		}
		time.Sleep(100 * time.Millisecond)
	}

	return nil
}

func demoIncrementalJog(ctx context.Context, client *LinuxCNCClient) error {
	fmt.Println("\n--- Incremental Jog Demo ---")
	fmt.Println("Jogging X axis +1.0 units...")

	// Jog X axis positive by 1.0 unit at 100 units/min
	resp, err := client.JogIncrement(ctx, 0, 100.0, 1.0)
	if err != nil {
		return err
	}
	if resp.Status != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("jog failed: %s", resp.ErrorMessage)
	}

	// Wait for motion to complete
	time.Sleep(1 * time.Second)

	// Show new position
	status, err := client.GetStatus(ctx)
	if err != nil {
		return err
	}
	pos := status.Position.ActualPosition
	fmt.Printf("New position: X=%.4f Y=%.4f Z=%.4f\n", pos.X, pos.Y, pos.Z)

	return nil
}

func demoContinuousJog(ctx context.Context, client *LinuxCNCClient) error {
	fmt.Println("\n--- Continuous Jog Demo ---")
	fmt.Println("Jogging Y axis positive for 0.5 seconds...")

	// Start continuous jog on Y axis at 50 units/min
	resp, err := client.JogContinuous(ctx, 1, 50.0)
	if err != nil {
		return err
	}
	if resp.Status != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("jog start failed: %s", resp.ErrorMessage)
	}

	// Let it jog for a bit
	time.Sleep(500 * time.Millisecond)

	// Stop the jog
	fmt.Println("Stopping jog...")
	resp, err = client.JogStop(ctx, 1)
	if err != nil {
		return err
	}
	if resp.Status != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("jog stop failed: %s", resp.ErrorMessage)
	}

	// Show new position
	status, err := client.GetStatus(ctx)
	if err != nil {
		return err
	}
	pos := status.Position.ActualPosition
	fmt.Printf("New position: X=%.4f Y=%.4f Z=%.4f\n", pos.X, pos.Y, pos.Z)

	return nil
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	skipDemo := flag.Bool("skip-demo", false, "Skip demo movements (just show status)")
	flag.Parse()

	client, err := NewLinuxCNCClient(*host, *port)
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer func() { _ = client.Close() }()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	// Show initial status
	status, err := client.GetStatus(ctx)
	if err != nil {
		log.Fatalf("GetStatus failed: %v", err)
	}
	pos := status.Position.ActualPosition
	fmt.Printf("Current position: X=%.4f Y=%.4f Z=%.4f\n", pos.X, pos.Y, pos.Z)

	if *skipDemo {
		fmt.Println("Skipping demo movements (--skip-demo)")
		return
	}

	// Ensure machine is ready for jogging
	if err := ensureMachineReady(ctx, client); err != nil {
		log.Fatalf("Could not prepare machine for jogging: %v", err)
	}

	// Run demos
	if err := demoIncrementalJog(ctx, client); err != nil {
		log.Fatalf("Incremental jog demo failed: %v", err)
	}
	if err := demoContinuousJog(ctx, client); err != nil {
		log.Fatalf("Continuous jog demo failed: %v", err)
	}

	fmt.Println("\nJog demo complete!")
}
