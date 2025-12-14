// Get LinuxCNC Status Example
//
// Connects to the gRPC server and displays the current machine status.
// This is the simplest example - a good starting point for understanding the API.
//
// Usage:
//
//	go run get_status.go [--host HOST] [--port PORT]
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	flag.Parse()

	addr := fmt.Sprintf("%s:%d", *host, *port)
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer conn.Close()

	client := pb.NewLinuxCNCServiceClient(conn)

	// Request current status
	status, err := client.GetStatus(context.Background(), &pb.GetStatusRequest{})
	if err != nil {
		log.Fatalf("GetStatus failed: %v", err)
	}

	// Print status
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("LinuxCNC Status")
	fmt.Println(strings.Repeat("=", 60))

	// Task status
	fmt.Println("\n[Task]")
	fmt.Printf("  Mode:       %s\n", status.Task.TaskMode.String())
	fmt.Printf("  State:      %s\n", status.Task.TaskState.String())
	fmt.Printf("  Exec State: %s\n", status.Task.ExecState.String())
	fmt.Printf("  Interp:     %s\n", status.Task.InterpState.String())
	if status.Task.File != "" {
		fmt.Printf("  File:       %s\n", status.Task.File)
	}

	// Position
	pos := status.Position.ActualPosition
	fmt.Println("\n[Position]")
	fmt.Printf("  X: %10.4f  Y: %10.4f  Z: %10.4f\n", pos.X, pos.Y, pos.Z)
	if pos.A != 0 || pos.B != 0 || pos.C != 0 {
		fmt.Printf("  A: %10.4f  B: %10.4f  C: %10.4f\n", pos.A, pos.B, pos.C)
	}

	// Trajectory
	fmt.Println("\n[Trajectory]")
	fmt.Printf("  Enabled:    %v\n", status.Trajectory.Enabled)
	fmt.Printf("  Feed Rate:  %.1f%%\n", status.Trajectory.Feedrate*100)
	fmt.Printf("  Rapid Rate: %.1f%%\n", status.Trajectory.Rapidrate*100)
	fmt.Printf("  Velocity:   %.2f\n", status.Trajectory.CurrentVel)

	// Joints
	fmt.Println("\n[Joints]")
	for _, joint := range status.Joints {
		homed := "-"
		if joint.Homed {
			homed = "H"
		}
		enabled := "-"
		if joint.Enabled {
			enabled = "E"
		}
		fault := "-"
		if joint.Fault {
			fault = "F"
		}
		fmt.Printf("  Joint %d: [%s%s%s] pos=%10.4f\n", joint.JointNumber, homed, enabled, fault, joint.Input)
	}

	// Spindles
	if len(status.Spindles) > 0 {
		fmt.Println("\n[Spindles]")
		for _, spindle := range status.Spindles {
			direction := "OFF"
			switch spindle.Direction {
			case -1:
				direction = "REV"
			case 1:
				direction = "FWD"
			}
			fmt.Printf("  Spindle %d: %s @ %.0f RPM\n", spindle.SpindleNumber, direction, spindle.Speed)
		}
	}

	// I/O
	fmt.Println("\n[I/O]")
	estopStr := "OK"
	if status.Io.Estop {
		estopStr = "ACTIVE"
	}
	fmt.Printf("  E-stop: %s\n", estopStr)
	fmt.Printf("  Mist:   %s\n", status.Io.Mist.String())
	fmt.Printf("  Flood:  %s\n", status.Io.Flood.String())

	// Active G-codes
	if len(status.Gcode.ActiveGcodes) > 0 {
		fmt.Println("\n[Active G-codes]")
		var gcodes []string
		for _, g := range status.Gcode.ActiveGcodes {
			if g > 0 {
				if g%10 == 0 {
					gcodes = append(gcodes, fmt.Sprintf("G%d", g/10))
				} else {
					gcodes = append(gcodes, fmt.Sprintf("G%.1f", float64(g)/10))
				}
			}
		}
		if len(gcodes) > 10 {
			fmt.Printf("  %s\n", strings.Join(gcodes[:10], " "))
			fmt.Printf("  %s\n", strings.Join(gcodes[10:], " "))
		} else {
			fmt.Printf("  %s\n", strings.Join(gcodes, " "))
		}
	}

	// Errors
	if len(status.Errors) > 0 {
		fmt.Println("\n[Errors]")
		for _, err := range status.Errors {
			fmt.Printf("  %s: %s\n", err.Type.String(), err.Message)
		}
	}

	fmt.Println()
}
