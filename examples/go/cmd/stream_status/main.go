// Stream Status Example
//
// Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
// This is useful for building dashboards or monitoring applications.
//
// Usage:
//
//	go run ./cmd/stream_status [--interval 100]
//
// --interval is the time between updates in milliseconds, from 1 to 60000.
//
// Press Ctrl+C to stop streaming. The example gives up, and exits non-zero,
// when no update has arrived for 30 seconds or for twice the interval plus 5
// seconds, whichever is longer. It also exits non-zero when the stream fails
// or the server cancels it.
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
	"sync/atomic"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

// The server accepts intervals up to 60 seconds.
const (
	minIntervalMs = 1
	maxIntervalMs = 60000
)

// A stream has no overall deadline: it runs until Ctrl+C. Instead, give up
// when the server has sent nothing for too long: the larger of
// minStreamIdleTimeout and streamIdleIntervals update intervals plus
// streamIdleMargin.
const (
	minStreamIdleTimeout = 30 * time.Second
	streamIdleIntervals  = 2
	streamIdleMargin     = 5 * time.Second
)

// streamIdleTimeout is the time without an update after which the stream
// counts as dead.
func streamIdleTimeout(intervalMs int) time.Duration {
	return max(minStreamIdleTimeout,
		streamIdleIntervals*time.Duration(intervalMs)*time.Millisecond+streamIdleMargin)
}

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
	intervalMs := flag.Int("interval", 100, "Update interval in milliseconds, 1 to 60000. The example gives up when no update arrives for 30 seconds or twice the interval plus 5 seconds, whichever is longer")
	flag.Parse()

	if *intervalMs < minIntervalMs || *intervalMs > maxIntervalMs {
		fmt.Fprintln(os.Stderr, "Error: --interval must be a whole number of milliseconds from 1 to 60000")
		os.Exit(1)
	}

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
	fmt.Println("Press Ctrl+C to stop")
	fmt.Println()
	fmt.Println(strings.Repeat("-", 80))

	updateCount := 0
	startTime := time.Now()

	// Cancels the stream when no update arrives in time; reset on every update.
	idleTimeout := streamIdleTimeout(*intervalMs)
	var streamWentSilent atomic.Bool
	idleTimer := time.AfterFunc(idleTimeout, func() {
		streamWentSilent.Store(true)
		cancel()
	})
	defer idleTimer.Stop()

	for {
		status, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			if ctx.Err() != nil {
				// Cancelled by this program: Ctrl+C or the idle timer
				break
			}
			// Anything else is a failure, including a stream the server
			// cancelled.
			log.Fatalf("Stream error: %v", err)
		}

		idleTimer.Reset(idleTimeout)
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

	if streamWentSilent.Load() {
		fmt.Fprintf(os.Stderr, "No status update for %g seconds; giving up.\n", idleTimeout.Seconds())
		os.Exit(1)
	}
}
