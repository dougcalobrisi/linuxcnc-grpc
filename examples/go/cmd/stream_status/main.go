// Stream Status Example
//
// Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
// This is useful for building dashboards or monitoring applications.
//
// Usage:
//
//	go run stream_status.go [--interval 100]
//
// Press Ctrl+C to stop streaming.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

func formatPosition(pos *pb.Position) string {
	return fmt.Sprintf("X:%8.3f Y:%8.3f Z:%8.3f", pos.X, pos.Y, pos.Z)
}

func formatState(status *pb.LinuxCNCStatus) string {
	mode := strings.Replace(status.Task.TaskMode.String(), "MODE_", "", 1)
	state := strings.Replace(status.Task.TaskState.String(), "STATE_", "", 1)
	interp := strings.Replace(status.Task.InterpState.String(), "INTERP_", "", 1)
	return fmt.Sprintf("%s/%s/%s", mode, state, interp)
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	intervalMs := flag.Int("interval", 100, "Update interval in milliseconds")
	flag.Parse()

	addr := fmt.Sprintf("%s:%d", *host, *port)
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer conn.Close()

	client := pb.NewLinuxCNCServiceClient(conn)

	// Set up cancellation on Ctrl+C
	ctx, cancel := context.WithCancel(context.Background())
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigChan
		cancel()
	}()

	// Start streaming
	stream, err := client.StreamStatus(ctx, &pb.StreamStatusRequest{
		IntervalMs: int32(*intervalMs),
	})
	if err != nil {
		log.Fatalf("StreamStatus failed: %v", err)
	}

	fmt.Printf("Streaming status from %s (interval: %dms)\n", addr, *intervalMs)
	fmt.Println("Press Ctrl+C to stop\n")
	fmt.Println(strings.Repeat("-", 80))

	updateCount := 0
	startTime := time.Now()

	for {
		status, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			if ctx.Err() != nil {
				// Cancelled, normal exit
				break
			}
			log.Fatalf("Stream error: %v", err)
		}

		updateCount++

		pos := formatPosition(status.Position.ActualPosition)
		state := formatState(status)
		vel := status.Trajectory.CurrentVel
		feed := status.Trajectory.Feedrate * 100

		// Format spindle info
		spindleInfo := ""
		if len(status.Spindles) > 0 && status.Spindles[0].Speed > 0 {
			spindleInfo = fmt.Sprintf(" S:%.0f", status.Spindles[0].Speed)
		}

		fmt.Printf("\r[%6d] %s | %-20s | V:%7.2f F:%5.1f%%%s  ",
			updateCount, pos, state, vel, feed, spindleInfo)
	}

	elapsed := time.Since(startTime).Seconds()
	fmt.Printf("\n\nReceived %d updates in %.1fs (%.1f updates/sec)\n",
		updateCount, elapsed, float64(updateCount)/elapsed)
}
