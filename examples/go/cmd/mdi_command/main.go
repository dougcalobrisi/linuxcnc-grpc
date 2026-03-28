// MDI Command Example
//
// Execute G-code commands via MDI (Manual Data Input) mode.
// This is useful for sending individual G-code commands without loading a file.
//
// Usage:
//
//	go run mdi_command.go "G0 X10 Y10"
//	go run mdi_command.go --interactive
//
// Safety Warning:
//
//	MDI commands execute immediately on the machine. Understand what
//	each command does before running it.
package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
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

func (c *LinuxCNCClient) WaitComplete(ctx context.Context, serial int32, timeout float64) (*pb.CommandResponse, error) {
	return c.client.WaitComplete(ctx, &pb.WaitCompleteRequest{
		Serial:  serial,
		Timeout: timeout,
	})
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

func (c *LinuxCNCClient) MDI(ctx context.Context, gcode string) (*pb.CommandResponse, int32, error) {
	cmd := &pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Mdi{
			Mdi: &pb.MdiCommand{Command: gcode},
		},
	}
	resp, err := c.SendCommand(ctx, cmd)
	return resp, cmd.Serial, err
}

func ensureMDIReady(ctx context.Context, client *LinuxCNCClient) error {
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

	// Set MDI mode
	if status.Task.TaskMode != pb.TaskMode_MODE_MDI {
		fmt.Println("Setting MDI mode...")
		resp, err := client.SetMode(ctx, pb.TaskMode_MODE_MDI)
		if err != nil {
			return err
		}
		if resp.Status != pb.RcsStatus_RCS_DONE {
			return fmt.Errorf("failed to set MDI mode: %s", resp.ErrorMessage)
		}
		time.Sleep(100 * time.Millisecond)
	}

	return nil
}

func executeMDI(ctx context.Context, client *LinuxCNCClient, gcode string, wait bool) error {
	fmt.Printf("Executing: %s\n", gcode)

	resp, serial, err := client.MDI(ctx, gcode)
	if err != nil {
		return err
	}
	if resp.Status == pb.RcsStatus_RCS_ERROR {
		return fmt.Errorf("error: %s", resp.ErrorMessage)
	}

	if wait {
		fmt.Println("  Waiting for completion...")
		resp, err = client.WaitComplete(ctx, serial, 60.0)
		if err != nil {
			return err
		}
		if resp.Status == pb.RcsStatus_RCS_ERROR {
			return fmt.Errorf("error during execution: %s", resp.ErrorMessage)
		}
		fmt.Println("  Done.")
	}

	return nil
}

func interactiveMode(ctx context.Context, client *LinuxCNCClient) {
	fmt.Println("\nInteractive MDI Mode")
	fmt.Println("Type G-code commands to execute. Type 'quit' or 'exit' to quit.")
	fmt.Println("Type 'status' to show current position.\n")

	scanner := bufio.NewScanner(os.Stdin)
	for {
		fmt.Print("MDI> ")
		if !scanner.Scan() {
			fmt.Println("\nExiting...")
			break
		}

		cmd := strings.TrimSpace(scanner.Text())
		if cmd == "" {
			continue
		}

		lower := strings.ToLower(cmd)
		if lower == "quit" || lower == "exit" || lower == "q" {
			break
		}

		if lower == "status" {
			status, err := client.GetStatus(ctx)
			if err != nil {
				fmt.Printf("Error: %v\n", err)
				continue
			}
			pos := status.Position.ActualPosition
			fmt.Printf("Position: X=%.4f Y=%.4f Z=%.4f\n", pos.X, pos.Y, pos.Z)
			continue
		}

		if lower == "help" {
			fmt.Println("Commands:")
			fmt.Println("  <G-code>  - Execute G-code command")
			fmt.Println("  status    - Show current position")
			fmt.Println("  quit      - Exit interactive mode")
			continue
		}

		// Ensure we're still in MDI mode
		status, err := client.GetStatus(ctx)
		if err != nil {
			fmt.Printf("Error: %v\n", err)
			continue
		}
		if status.Task.TaskMode != pb.TaskMode_MODE_MDI {
			if err := ensureMDIReady(ctx, client); err != nil {
				fmt.Printf("Failed to re-enter MDI mode: %v\n", err)
				continue
			}
		}

		if err := executeMDI(ctx, client, cmd, true); err != nil {
			fmt.Printf("Error: %v\n", err)
		}
	}
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	interactive := flag.Bool("interactive", false, "Enter interactive MDI mode")
	interactive2 := flag.Bool("i", false, "Enter interactive MDI mode (shorthand)")
	noWait := flag.Bool("no-wait", false, "Don't wait for command completion")
	flag.Parse()

	isInteractive := *interactive || *interactive2
	command := ""
	if flag.NArg() > 0 {
		command = flag.Arg(0)
	}

	if command == "" && !isInteractive {
		fmt.Println("Usage: go run mdi_command.go [options] \"G-code command\"")
		fmt.Println("       go run mdi_command.go --interactive")
		fmt.Println("\nOptions:")
		flag.PrintDefaults()
		fmt.Println("\nExamples:")
		fmt.Println("  go run mdi_command.go \"G0 X10 Y10\"")
		fmt.Println("  go run mdi_command.go \"G1 X20 F100\"")
		fmt.Println("  go run mdi_command.go --interactive")
		os.Exit(1)
	}

	client, err := NewLinuxCNCClient(*host, *port)
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer func() { _ = client.Close() }()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	// Ensure machine is ready for MDI
	if err := ensureMDIReady(ctx, client); err != nil {
		log.Fatalf("Could not prepare machine for MDI: %v", err)
	}

	if isInteractive {
		// Interactive mode runs indefinitely, use background context
		interactiveMode(context.Background(), client)
	} else {
		if err := executeMDI(ctx, client, command, !*noWait); err != nil {
			log.Fatalf("MDI command failed: %v", err)
		}

		// Show final position
		status, err := client.GetStatus(ctx)
		if err != nil {
			log.Fatalf("GetStatus failed: %v", err)
		}
		pos := status.Position.ActualPosition
		fmt.Printf("Position: X=%.4f Y=%.4f Z=%.4f\n", pos.X, pos.Y, pos.Z)
	}
}
