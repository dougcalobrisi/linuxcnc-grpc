// MDI Command Example
//
// Execute G-code commands via MDI (Manual Data Input) mode.
// This is useful for sending individual G-code commands without loading a file.
//
// Usage:
//
//	go run ./cmd/mdi_command "G0 X10 Y10"
//	go run ./cmd/mdi_command --interactive
//
// Flags must come before the G-code command.
//
// Before it sends anything the example checks that the machine is on, that
// every joint is homed and that the interpreter is idle. It does not take the
// machine out of E-stop, power it on or home it: do that at the machine first.
// It then switches to MDI mode. The checks are repeated before every command,
// so in interactive mode a command is not sent while an earlier one is still
// running.
//
// Safety Warning:
//
//	MDI commands execute immediately on the machine. Understand what
//	each command does before running it. Ctrl+C stops this program, not the
//	machine: a command that was already sent keeps running.
package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"math"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

// Deadlines. Every RPC gets one.
const (
	rpcTimeout         = 5 * time.Second  // GetStatus and SendCommand
	stateChangeTimeout = 5 * time.Second  // A mode change must finish within this
	waitDeadlineMargin = 5 * time.Second  // WaitComplete's client deadline = its timeout + this
	defaultMDITimeout  = 60 * time.Second // How long to wait for an MDI command to finish
	maxMDITimeout      = 24 * time.Hour   // Longest --wait-timeout accepted (one day)
	pollInterval       = 50 * time.Millisecond
)

// Below this velocity the machine is standing still.
const stoppedVelocity = 1e-6

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

// GetStatus fetches the current status with its own deadline.
func (c *LinuxCNCClient) GetStatus() (*pb.LinuxCNCStatus, error) {
	ctx, cancel := context.WithTimeout(context.Background(), rpcTimeout)
	defer cancel()
	return c.client.GetStatus(ctx, &pb.GetStatusRequest{})
}

// SendCommand sends a command with a fresh deadline. RCS_DONE in the response
// means dispatched, not finished.
func (c *LinuxCNCClient) SendCommand(cmd *pb.LinuxCNCCommand) (*pb.CommandResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), rpcTimeout)
	defer cancel()
	cmd.Serial = c.nextSerial()
	cmd.Timestamp = time.Now().UnixNano()
	return c.client.SendCommand(ctx, cmd)
}

// WaitComplete waits until the command that sent answers has finished. The
// result is RCS_DONE, RCS_ERROR (failed) or RCS_EXEC (the timeout expired and
// the command is still running). RCS_DONE means finished only if LinuxCNC
// took no later command meanwhile; see confirmMDIFinished.
func (c *LinuxCNCClient) WaitComplete(sent *pb.CommandResponse, timeout time.Duration) (*pb.CommandResponse, error) {
	request := &pb.WaitCompleteRequest{Timeout: timeout.Seconds()}
	if sent.LinuxcncSerial != nil {
		// Wait on this command's serial. WaitCompleteRequest.Serial would
		// not do that: the server ignores it.
		request.LinuxcncSerial = sent.LinuxcncSerial
	} else {
		fmt.Println("  Note: the server returned no linuxcnc_serial; waiting on the latest command sent by any client instead.")
	}
	// The client deadline must be longer than the wait asked of the server.
	ctx, cancel := context.WithTimeout(context.Background(), timeout+waitDeadlineMargin)
	defer cancel()
	return c.client.WaitComplete(ctx, request)
}

func (c *LinuxCNCClient) SetMode(mode pb.TaskMode) (*pb.CommandResponse, error) {
	return c.SendCommand(&pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Mode{
			Mode: &pb.ModeCommand{Mode: mode},
		},
	})
}

// MDI sends an MDI command.
func (c *LinuxCNCClient) MDI(gcode string) (*pb.CommandResponse, error) {
	return c.SendCommand(&pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Mdi{
			Mdi: &pb.MdiCommand{Command: gcode},
		},
	})
}

func machineIsOn(status *pb.LinuxCNCStatus) bool {
	return status.GetTask().GetTaskState() == pb.TaskState_STATE_ON
}

// machineIsBusy is true while a program, an MDI command or a jog is under way.
//
// The interpreter state alone does not tell. On the LinuxCNC 2.9 simulator it
// read INTERP_IDLE for the whole of some MDI moves, while Trajectory.Inpos
// was false in every reading taken during a move. A jog leaves Inpos true, so
// velocity is looked at as well.
func machineIsBusy(status *pb.LinuxCNCStatus) bool {
	return status.GetTask().GetInterpState() != pb.InterpState_INTERP_IDLE ||
		!status.GetTrajectory().GetInpos() ||
		math.Abs(status.GetTrajectory().GetCurrentVel()) >= stoppedVelocity
}

// ensureMDIReady checks the machine is on, homed and idle, then makes sure it
// is in MDI mode.
func ensureMDIReady(client *LinuxCNCClient) error {
	status, err := client.GetStatus()
	if err != nil {
		return err
	}

	// Never take the machine out of E-stop or power it on from here.
	if !machineIsOn(status) {
		return fmt.Errorf("the machine is not on (state %s). Take it out of E-stop and turn it on at the machine, then run this again",
			status.GetTask().GetTaskState())
	}

	// A 1.0 server lists 16 joint entries, of which only the first
	// Trajectory.Joints are real. Newer servers list only those.
	var unhomedJoints []int32
	for jointIndex, joint := range status.GetJoints() {
		if int32(jointIndex) < status.GetTrajectory().GetJoints() && !joint.GetHomed() {
			unhomedJoints = append(unhomedJoints, joint.GetJointNumber())
		}
	}
	if len(unhomedJoints) > 0 {
		return fmt.Errorf("joints %v are not homed. Home the machine first; this example does not home it", unhomedJoints)
	}

	// Checked every time, also when the machine is already in MDI mode: a
	// command whose wait timed out is still running, and so may a program be.
	if machineIsBusy(status) {
		return fmt.Errorf("the machine is still busy with a previous command, a running program or a jog (interpreter %s, in position: %t, velocity %g). Nothing was sent; wait for it to finish and try again",
			status.GetTask().GetInterpState(), status.GetTrajectory().GetInpos(), status.GetTrajectory().GetCurrentVel())
	}

	if status.GetTask().GetTaskMode() == pb.TaskMode_MODE_MDI {
		return nil
	}

	fmt.Println("Setting MDI mode...")
	sent, err := client.SetMode(pb.TaskMode_MODE_MDI)
	if err != nil {
		return err
	}
	if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("MDI mode was refused: %s", sent.GetErrorMessage())
	}

	finished, err := client.WaitComplete(sent, stateChangeTimeout)
	if err != nil {
		return err
	}
	switch finished.GetStatus() {
	case pb.RcsStatus_RCS_DONE:
	case pb.RcsStatus_RCS_EXEC:
		return fmt.Errorf("MDI mode had not finished after %v", stateChangeTimeout)
	default:
		return fmt.Errorf("MDI mode failed: %s", finished.GetErrorMessage())
	}

	// Finished is not the same as in effect: check the status, with a deadline.
	deadline := time.Now().Add(stateChangeTimeout)
	for {
		status, err := client.GetStatus()
		if err != nil {
			return err
		}
		if status.GetTask().GetTaskMode() == pb.TaskMode_MODE_MDI {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("MDI mode did not show in the status within %v", stateChangeTimeout)
		}
		time.Sleep(pollInterval)
	}
}

// confirmMDIFinished polls the status until it shows that the MDI command is
// no longer running.
//
// RCS_DONE from WaitComplete is not enough. The server also answers it as
// soon as LinuxCNC has taken a later command from any client (the GUI
// changing the feed override, say), and the MDI move may then still be
// running. The status settles it: machineIsBusy is false again.
//
// The machine must also still be on. An E-stop from another client ends the
// wait with RCS_DONE too, and the status then reads idle, in position and
// standing still although the move was cut short.
//
// One case cannot be told from completion here: an abort from another
// client. It stops the move and leaves the machine on, idle and in
// position, exactly as a finished command does. Only the position shows
// it: the move ends short of its target.
//
// It returns false if the status did not show that by the deadline. The
// status is read at least once, and each read has its own deadline. It
// returns an error if the machine is not on.
func confirmMDIFinished(client *LinuxCNCClient, deadline time.Time) (bool, error) {
	for {
		status, err := client.GetStatus()
		if err != nil {
			return false, err
		}
		if !machineIsOn(status) {
			return false, fmt.Errorf("the machine is no longer on (state %s). The command was stopped and did not finish",
				status.GetTask().GetTaskState())
		}
		if !machineIsBusy(status) {
			return true, nil
		}
		if !time.Now().Before(deadline) {
			return false, nil
		}
		time.Sleep(pollInterval)
	}
}

// executeMDI sends an MDI command and, if asked, waits until it has finished.
func executeMDI(client *LinuxCNCClient, gcode string, wait bool, waitTimeout time.Duration) error {
	fmt.Printf("Executing: %s\n", gcode)

	sent, err := client.MDI(gcode)
	if err != nil {
		return err
	}
	if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("the command was refused: %s", sent.GetErrorMessage())
	}

	if !wait {
		fmt.Println("  Dispatched (not waiting for it to finish).")
		return nil
	}

	fmt.Println("  Waiting for completion...")
	// One timeout covers both the wait and the check of the status after it.
	waitDeadline := time.Now().Add(waitTimeout)
	finished, err := client.WaitComplete(sent, waitTimeout)
	if err != nil {
		return err
	}
	switch finished.GetStatus() {
	case pb.RcsStatus_RCS_DONE:
	case pb.RcsStatus_RCS_EXEC:
		return fmt.Errorf("timed out: the command was still running after %v. It has not been cancelled", waitTimeout)
	default:
		return fmt.Errorf("the command failed: %s", finished.GetErrorMessage())
	}

	isConfirmed, err := confirmMDIFinished(client, waitDeadline)
	if err != nil {
		return err
	}
	if !isConfirmed {
		return fmt.Errorf("not confirmed: the wait ended, but the status did not show the interpreter idle and the motion in position within %v. The command may still be running (a wait ends early when LinuxCNC takes a later command from any client). It has not been cancelled", waitTimeout)
	}
	fmt.Println("  Done.")
	return nil
}

func printPosition(client *LinuxCNCClient) error {
	status, err := client.GetStatus()
	if err != nil {
		return err
	}
	position := status.GetPosition().GetActualPosition()
	fmt.Printf("Position: X=%.4f Y=%.4f Z=%.4f\n", position.GetX(), position.GetY(), position.GetZ())
	return nil
}

// interactiveMode runs the MDI shell. A command that fails does not end the
// session, but it is counted, and the count comes back as an error so the
// program exits non-zero. So does a failure to read the input.
func interactiveMode(client *LinuxCNCClient, waitTimeout time.Duration) error {
	failedCommandCount := 0
	fmt.Println("\nInteractive MDI Mode")
	fmt.Println("Type G-code commands to execute. Type 'quit' or 'exit' to quit.")
	fmt.Println("Type 'status' to show current position.")
	fmt.Println()

	scanner := bufio.NewScanner(os.Stdin)
	for {
		fmt.Print("MDI> ")
		if !scanner.Scan() {
			// Scan also returns false on a read error and on a line too long
			// for its buffer. Neither is the end of the input.
			if err := scanner.Err(); err != nil {
				fmt.Println()
				return fmt.Errorf("reading input failed: %w", err)
			}
			fmt.Println("\nExiting...")
			break
		}

		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		lower := strings.ToLower(line)
		if lower == "quit" || lower == "exit" || lower == "q" {
			break
		}

		if lower == "status" {
			if err := printPosition(client); err != nil {
				failedCommandCount++
				fmt.Printf("  Error: %v\n", err)
			}
			continue
		}

		if lower == "help" {
			fmt.Println("Commands:")
			fmt.Println("  <G-code>  - Execute G-code command")
			fmt.Println("  status    - Show current position")
			fmt.Println("  quit      - Exit interactive mode")
			continue
		}

		// The machine may have been turned off or switched to another mode
		// since the last command, or the last command may still be running.
		if err := ensureMDIReady(client); err != nil {
			failedCommandCount++
			fmt.Printf("  Error: %v\n", err)
			continue
		}
		if err := executeMDI(client, line, true, waitTimeout); err != nil {
			failedCommandCount++
			fmt.Printf("  Error: %v\n", err)
		}
	}

	if failedCommandCount > 0 {
		return fmt.Errorf("%d command(s) failed in this session", failedCommandCount)
	}
	return nil
}

func run(host string, port int, command string, isInteractive, wait bool, waitTimeout time.Duration) error {
	client, err := NewLinuxCNCClient(host, port)
	if err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer func() { _ = client.Close() }()

	if err := ensureMDIReady(client); err != nil {
		return err
	}

	if isInteractive {
		return interactiveMode(client, waitTimeout)
	}

	if err := executeMDI(client, command, wait, waitTimeout); err != nil {
		return err
	}
	return printPosition(client)
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	interactive := flag.Bool("interactive", false, "Enter interactive MDI mode")
	interactiveShorthand := flag.Bool("i", false, "Enter interactive MDI mode (shorthand)")
	noWait := flag.Bool("no-wait", false, "Don't wait for command completion")
	waitTimeoutSeconds := flag.Float64("wait-timeout", defaultMDITimeout.Seconds(), "Seconds to wait for a command to finish; more than 0 and at most 86400")
	flag.Parse()

	isInteractive := *interactive || *interactiveShorthand
	command := ""
	if flag.NArg() > 0 {
		command = flag.Arg(0)
	}

	// The flag package stops at the first argument that is not a flag, so a
	// flag written after the G-code (or after "--") would be taken as G-code.
	if flag.NArg() > 1 || strings.HasPrefix(command, "-") {
		fmt.Fprintln(os.Stderr, "Put the flags before the G-code command, and quote the command as one argument.")
		os.Exit(1)
	}

	if command == "" && !isInteractive {
		fmt.Println("Usage: go run ./cmd/mdi_command [options] \"G-code command\"")
		fmt.Println("       go run ./cmd/mdi_command --interactive")
		fmt.Println("\nOptions:")
		flag.PrintDefaults()
		fmt.Println("\nExamples:")
		fmt.Println("  go run ./cmd/mdi_command \"G0 X10 Y10\"")
		fmt.Println("  go run ./cmd/mdi_command \"G1 X20 F100\"")
		fmt.Println("  go run ./cmd/mdi_command --interactive")
		os.Exit(1)
	}

	// Checked before any RPC: 0 would make the server use its own default, and
	// a negative or huge value would give a client deadline that has already
	// expired or cannot be represented, after the command was dispatched.
	// NaN fails every comparison, so it is rejected here too.
	if math.IsInf(*waitTimeoutSeconds, 0) || !(*waitTimeoutSeconds > 0 && *waitTimeoutSeconds <= maxMDITimeout.Seconds()) {
		fmt.Fprintln(os.Stderr, "Error: --wait-timeout must be a number of seconds greater than 0 and at most 86400")
		os.Exit(1)
	}

	waitTimeout := time.Duration(*waitTimeoutSeconds * float64(time.Second))
	if err := run(*host, *port, command, isInteractive, !*noWait, waitTimeout); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}
