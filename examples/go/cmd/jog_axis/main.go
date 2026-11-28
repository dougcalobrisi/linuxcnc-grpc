// Jog Axis Example
//
// Jogs the X axis by a fixed increment, then jogs the Y axis continuously for
// half a second and stops it.
//
// Usage:
//
//	go run ./cmd/jog_axis [--host HOST] [--port PORT] [--skip-demo]
//
// Before it moves anything the example checks that the machine is on, that
// every joint is homed and that the interpreter is idle. It does not take the
// machine out of E-stop, power it on or home it: do that at the machine first.
// It then switches to manual mode and turns teleop on, which is what an axis
// jog needs. The checks are repeated on a fresh status right before each jog
// is started.
//
// Safety Warning:
//
//	This program moves the machine! Ensure you have clear access to E-stop
//	and understand the jog parameters before running.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"math"
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

// Deadlines. Every RPC gets one.
const (
	rpcTimeout         = 5 * time.Second  // GetStatus and SendCommand
	jogStopTimeout     = 2 * time.Second  // The jog stop has its own short deadline
	stateChangeTimeout = 5 * time.Second  // A mode or teleop change must finish within this
	waitDeadlineMargin = 5 * time.Second  // WaitComplete's client deadline = its timeout + this
	motionStartTimeout = 2 * time.Second  // A jog that has not moved by now was refused
	motionTimeout      = 10 * time.Second // A jog must be finished and stopped within this
	pollInterval       = 50 * time.Millisecond
)

// The demo moves. Jog velocity is in machine units per second.
const (
	axisLetters          = "XYZABCUVW"
	incrementAxis        = int32(0) // X
	continuousAxis       = int32(1) // Y
	jogVelocity          = 1.0      // Machine units per second
	incrementDistance    = 1.0      // Machine units
	continuousJogSeconds = 0.5
	movedThreshold       = 0.01 // Machine units; more than feedback jitter, far less than either jog
	stoppedVelocity      = 1e-6
)

var errInterrupted = errors.New("interrupted")

// jogStopError says that a jog stop was not dispatched. It is its own type so
// that main can tell it from every other error: a failed stop decides the
// exit code, even when Ctrl+C was what led to the stop.
type jogStopError struct {
	message string
}

func (stopError *jogStopError) Error() string {
	return stopError.message
}

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
func (c *LinuxCNCClient) SendCommand(cmd *pb.LinuxCNCCommand, timeout time.Duration) (*pb.CommandResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd.Serial = c.nextSerial()
	cmd.Timestamp = time.Now().UnixNano()
	return c.client.SendCommand(ctx, cmd)
}

// WaitComplete waits until the command that sent answers has finished.
func (c *LinuxCNCClient) WaitComplete(sent *pb.CommandResponse, timeout time.Duration) (*pb.CommandResponse, error) {
	request := &pb.WaitCompleteRequest{Timeout: timeout.Seconds()}
	if sent.LinuxcncSerial != nil {
		// Wait on this command's serial.
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
	}, rpcTimeout)
}

// SetTeleop turns teleop on (axis jogs) or off (joint jogs, homing).
func (c *LinuxCNCClient) SetTeleop(enable bool) (*pb.CommandResponse, error) {
	return c.SendCommand(&pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Teleop{
			Teleop: &pb.TeleopCommand{Enable: enable},
		},
	}, rpcTimeout)
}

// JogContinuous starts a continuous axis jog. velocity > 0 for positive, < 0 for negative.
func (c *LinuxCNCClient) JogContinuous(axis int32, velocity float64) (*pb.CommandResponse, error) {
	return c.SendCommand(&pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Jog{
			Jog: &pb.JogCommand{
				Type:     pb.JogType_JOG_CONTINUOUS,
				IsJoint:  false, // Axis jog: needs teleop on
				Index:    axis,  // 0=X, 1=Y, 2=Z, etc.
				Velocity: velocity,
			},
		},
	}, rpcTimeout)
}

// JogIncrement jogs an axis by a fixed distance.
func (c *LinuxCNCClient) JogIncrement(axis int32, velocity, distance float64) (*pb.CommandResponse, error) {
	return c.SendCommand(&pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Jog{
			Jog: &pb.JogCommand{
				Type:      pb.JogType_JOG_INCREMENT,
				IsJoint:   false,
				Index:     axis,
				Velocity:  velocity,
				Increment: distance,
			},
		},
	}, rpcTimeout)
}

// JogStop stops jogging an axis. It uses its own short deadline.
func (c *LinuxCNCClient) JogStop(axis int32) (*pb.CommandResponse, error) {
	return c.SendCommand(&pb.LinuxCNCCommand{
		Command: &pb.LinuxCNCCommand_Jog{
			Jog: &pb.JogCommand{
				Type:    pb.JogType_JOG_STOP,
				IsJoint: false,
				Index:   axis,
			},
		},
	}, jogStopTimeout)
}

func axisLetter(axis int32) string {
	return string(axisLetters[axis])
}

func axisPosition(status *pb.LinuxCNCStatus, axis int32) float64 {
	position := status.GetPosition().GetActualPosition()
	return []float64{
		position.GetX(), position.GetY(), position.GetZ(),
		position.GetA(), position.GetB(), position.GetC(),
		position.GetU(), position.GetV(), position.GetW(),
	}[axis]
}

// keepRunningWhenOutputIsLost makes a write to a closed pipe return an error
// instead of ending the program. Without it, Go exits on SIGPIPE when standard
// output or standard error is a pipe whose reader has gone, and that could
// happen between starting a jog and stopping it. The fmt.Print calls ignore
// the write error, so the program carries on to the stop.
//
// On Windows there is no SIGPIPE and this does nothing.
func keepRunningWhenOutputIsLost() {
	signal.Ignore(syscall.SIGPIPE)
}

// pause sleeps, but returns errInterrupted as soon as Ctrl+C or SIGTERM arrives.
func pause(interrupt context.Context, duration time.Duration) error {
	if interrupt.Err() != nil {
		return errInterrupted
	}
	select {
	case <-interrupt.Done():
		return errInterrupted
	case <-time.After(duration):
		return nil
	}
}

// recentOperatorMessages gives the newest messages the server has read from
// LinuxCNC, if it reads them.
func recentOperatorMessages(status *pb.LinuxCNCStatus) string {
	messages := status.GetErrors()
	if len(messages) == 0 {
		return "Look at the operator messages in the LinuxCNC GUI for the reason."
	}
	if len(messages) > 3 {
		messages = messages[len(messages)-3:]
	}
	texts := make([]string, 0, len(messages))
	for _, message := range messages {
		texts = append(texts, message.GetMessage())
	}
	return "Recent LinuxCNC messages: " + strings.Join(texts, "; ")
}

// changeAndConfirm waits for a mode or teleop command to finish, then checks
// that the status shows it.
func changeAndConfirm(interrupt context.Context, client *LinuxCNCClient, sent *pb.CommandResponse,
	description string, isConfirmed func(*pb.LinuxCNCStatus) bool) error {
	if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("%s was refused: %s", description, sent.GetErrorMessage())
	}

	finished, err := client.WaitComplete(sent, stateChangeTimeout)
	if err != nil {
		return err
	}
	switch finished.GetStatus() {
	case pb.RcsStatus_RCS_DONE:
	case pb.RcsStatus_RCS_EXEC:
		return fmt.Errorf("%s had not finished after %v", description, stateChangeTimeout)
	default:
		return fmt.Errorf("%s failed: %s", description, finished.GetErrorMessage())
	}

	// Finished is not the same as in effect: check the status, with a deadline.
	deadline := time.Now().Add(stateChangeTimeout)
	for {
		status, err := client.GetStatus()
		if err != nil {
			return err
		}
		if isConfirmed(status) {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("%s did not show in the status within %v", description, stateChangeTimeout)
		}
		if err := pause(interrupt, pollInterval); err != nil {
			return err
		}
	}
}

// requireMachineOn returns an error unless the machine is on. It is never
// turned on from here.
func requireMachineOn(status *pb.LinuxCNCStatus) error {
	if status.GetTask().GetTaskState() != pb.TaskState_STATE_ON {
		return fmt.Errorf("the machine is not on (state %s). Take it out of E-stop and turn it on at the machine, then run this again",
			status.GetTask().GetTaskState())
	}
	return nil
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

// readyStatusForJog fetches a fresh status and checks that the machine is on,
// homed and idle.
//
// It is called before the mode is changed and again right before every jog is
// started: the machine can be put in E-stop, turned off or given a program at
// any moment, so an earlier answer is not good enough. Only the start of a
// jog depends on this check; a jog stop is always sent.
func readyStatusForJog(client *LinuxCNCClient) (*pb.LinuxCNCStatus, error) {
	status, err := client.GetStatus()
	if err != nil {
		return nil, err
	}

	// Never take the machine out of E-stop or power it on from here.
	if err := requireMachineOn(status); err != nil {
		return nil, err
	}

	// A 1.0 server lists 16 joint entries, of which only the first
	// Trajectory.Joints are real. Newer servers list only those.
	// An axis jog needs teleop, and teleop needs every joint homed.
	var unhomedJoints []int32
	for jointIndex, joint := range status.GetJoints() {
		if int32(jointIndex) < status.GetTrajectory().GetJoints() && !joint.GetHomed() {
			unhomedJoints = append(unhomedJoints, joint.GetJointNumber())
		}
	}
	if len(unhomedJoints) > 0 {
		return nil, fmt.Errorf("joints %v are not homed. Home the machine first; this example does not home it", unhomedJoints)
	}

	for _, axis := range []int32{incrementAxis, continuousAxis} {
		if status.GetTrajectory().GetAxisMask()&(1<<axis) == 0 {
			return nil, fmt.Errorf("this machine has no %s axis", axisLetter(axis))
		}
	}

	// Checked whatever the mode is: never jog, or change mode, under a
	// running program, MDI command or jog.
	if machineIsBusy(status) {
		return nil, fmt.Errorf("the machine is busy with a program, an MDI command or a jog (interpreter %s, in position: %t, velocity %g). The jog was not started; wait for it to finish and run this again",
			status.GetTask().GetInterpState(), status.GetTrajectory().GetInpos(), status.GetTrajectory().GetCurrentVel())
	}

	return status, nil
}

// prepareForAxisJog checks the machine is on, homed and idle, then sets manual
// mode and teleop.
func prepareForAxisJog(interrupt context.Context, client *LinuxCNCClient) error {
	status, err := readyStatusForJog(client)
	if err != nil {
		return err
	}

	if status.GetTask().GetTaskMode() != pb.TaskMode_MODE_MANUAL {
		fmt.Println("Setting manual mode...")
		sent, err := client.SetMode(pb.TaskMode_MODE_MANUAL)
		if err != nil {
			return err
		}
		err = changeAndConfirm(interrupt, client, sent, "Manual mode", func(newStatus *pb.LinuxCNCStatus) bool {
			return newStatus.GetTask().GetTaskMode() == pb.TaskMode_MODE_MANUAL
		})
		if err != nil {
			return err
		}
		if status, err = client.GetStatus(); err != nil {
			return err
		}
	}

	if status.GetTrajectory().GetMotionMode() != pb.TrajMode_TRAJ_MODE_TELEOP {
		fmt.Println("Turning teleop on...")
		sent, err := client.SetTeleop(true)
		if err != nil {
			return err
		}
		return changeAndConfirm(interrupt, client, sent, "Teleop", func(newStatus *pb.LinuxCNCStatus) bool {
			return newStatus.GetTrajectory().GetMotionMode() == pb.TrajMode_TRAJ_MODE_TELEOP
		})
	}

	return nil
}

// stopJog sends the jog stop and insists that it was dispatched.
func stopJog(client *LinuxCNCClient, axis int32) error {
	sent, err := client.JogStop(axis)
	if err != nil {
		return &jogStopError{fmt.Sprintf("the jog stop for %s did not get through (%v). The axis may still be moving: use the E-stop",
			axisLetter(axis), err)}
	}
	if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
		return &jogStopError{fmt.Sprintf("the jog stop for %s was refused: %s. The axis may still be moving: use the E-stop",
			axisLetter(axis), sent.GetErrorMessage())}
	}
	return nil
}

// reportStopFailureBeforePanic prints a failed stop to standard error. A
// panic that is about to resume would otherwise lose it. A failed write is
// ignored.
func reportStopFailureBeforePanic(stopErr error) {
	if stopErr != nil {
		_, _ = fmt.Fprintf(os.Stderr, "Error: %v\n", stopErr)
	}
}

// waitForMotionToEnd waits until the axis has moved and stopped again, and
// returns its position.
//
// A jog that LinuxCNC refuses still answers RCS_DONE, so the only proof of a
// jog is the status. Trajectory.Inpos stays true during a jog; velocity is
// what shows motion.
//
// The machine state is checked on every poll. After an E-stop the axis stops
// at once, but the velocity in the status keeps its last value until the
// machine is turned on again, so velocity alone would never show the stop.
func waitForMotionToEnd(interrupt context.Context, client *LinuxCNCClient, axis int32, startPosition float64) (float64, error) {
	startedWaiting := time.Now()
	for {
		status, err := client.GetStatus()
		if err != nil {
			return 0, err
		}
		if err := requireMachineOn(status); err != nil {
			return 0, err
		}
		position := axisPosition(status, axis)
		hasMoved := math.Abs(position-startPosition) > movedThreshold
		isStopped := math.Abs(status.GetTrajectory().GetCurrentVel()) < stoppedVelocity
		if hasMoved && isStopped {
			return position, nil
		}

		waited := time.Since(startedWaiting)
		if !hasMoved && waited >= motionStartTimeout {
			return 0, fmt.Errorf("%s did not move, so LinuxCNC refused the jog. %s",
				axisLetter(axis), recentOperatorMessages(status))
		}
		if waited >= motionTimeout {
			return 0, fmt.Errorf("%s was still moving after %v", axisLetter(axis), motionTimeout)
		}
		if err := pause(interrupt, pollInterval); err != nil {
			return 0, err
		}
	}
}

// demoIncrementalJog jogs one axis by a fixed distance and waits until it has
// arrived.
func demoIncrementalJog(interrupt context.Context, client *LinuxCNCClient) (err error) {
	fmt.Println("\n--- Incremental Jog Demo ---")
	fmt.Printf("Jogging %s by +%g at %g units/s...\n", axisLetter(incrementAxis), incrementDistance, jogVelocity)

	// The check is repeated here, on a fresh status, right before the start.
	status, err := readyStatusForJog(client)
	if err != nil {
		return err
	}
	startPosition := axisPosition(status, incrementAxis)

	// Do not start a jog after an interrupt.
	if err := pause(interrupt, 0); err != nil {
		return err
	}

	// From here on a write to a closed pipe must not end the program before
	// the stop is sent.
	keepRunningWhenOutputIsLost()

	// Interrupted, failed part way (including a start whose answer never
	// came) or panicked: do not leave the axis moving. When the wait succeeds
	// no stop is needed: the status showed the axis moved and is standing
	// still. Nothing is printed between the start and this stop.
	defer func() {
		panicValue := recover()
		if err != nil || panicValue != nil {
			stopErr := stopJog(client, incrementAxis)
			err = errors.Join(err, stopErr)
			if panicValue != nil {
				reportStopFailureBeforePanic(stopErr)
				panic(panicValue)
			}
		}
	}()

	sent, err := client.JogIncrement(incrementAxis, jogVelocity, incrementDistance)
	if err != nil {
		return err
	}
	if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("incremental jog was refused: %s", sent.GetErrorMessage())
	}

	endPosition, err := waitForMotionToEnd(interrupt, client, incrementAxis, startPosition)
	if err != nil {
		return err
	}
	fmt.Printf("%s moved from %.4f to %.4f\n", axisLetter(incrementAxis), startPosition, endPosition)
	return nil
}

// runContinuousJogAndStop starts the jog, keeps it going for the demo time
// and then stops it.
//
// The stop is always sent: after the sleep, on Ctrl+C, on any error
// (including a start whose answer never came) and on a panic. It has its own
// fresh deadline, so it still goes out when another deadline has expired.
// Nothing is printed between the start and the stop.
func runContinuousJogAndStop(interrupt context.Context, client *LinuxCNCClient) (err error) {
	defer func() {
		panicValue := recover()
		stopErr := stopJog(client, continuousAxis)
		err = errors.Join(err, stopErr)
		if panicValue != nil {
			reportStopFailureBeforePanic(stopErr)
			panic(panicValue)
		}
	}()

	sent, err := client.JogContinuous(continuousAxis, jogVelocity)
	if err != nil {
		return err
	}
	if sent.GetStatus() != pb.RcsStatus_RCS_DONE {
		return fmt.Errorf("continuous jog was refused: %s", sent.GetErrorMessage())
	}
	return pause(interrupt, time.Duration(continuousJogSeconds*float64(time.Second)))
}

// demoContinuousJog jogs one axis continuously for a moment, then stops it.
func demoContinuousJog(interrupt context.Context, client *LinuxCNCClient) error {
	fmt.Println("\n--- Continuous Jog Demo ---")
	fmt.Printf("Jogging %s at +%g units/s for %g seconds...\n", axisLetter(continuousAxis), jogVelocity, continuousJogSeconds)

	// The check is repeated here, on a fresh status, right before the start.
	status, err := readyStatusForJog(client)
	if err != nil {
		return err
	}
	startPosition := axisPosition(status, continuousAxis)

	// Do not start a jog after an interrupt.
	if err := pause(interrupt, 0); err != nil {
		return err
	}

	// From here on a write to a closed pipe must not end the program before
	// the stop is sent.
	keepRunningWhenOutputIsLost()

	if err := runContinuousJogAndStop(interrupt, client); err != nil {
		return err
	}
	fmt.Println("Jog stop sent.")

	endPosition, err := waitForMotionToEnd(interrupt, client, continuousAxis, startPosition)
	if err != nil {
		return err
	}
	fmt.Printf("%s moved from %.4f to %.4f\n", axisLetter(continuousAxis), startPosition, endPosition)
	return nil
}

func run(host string, port int, skipDemo bool) error {
	// Installed before anything moves. Ctrl+C and SIGTERM only cancel this
	// context; the waits watch it, so the program always leaves through the
	// code that stops the jog.
	interrupt, stopWatchingSignals := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopWatchingSignals()

	client, err := NewLinuxCNCClient(host, port)
	if err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer func() { _ = client.Close() }()

	// Show initial status
	status, err := client.GetStatus()
	if err != nil {
		return fmt.Errorf("GetStatus failed: %w", err)
	}
	position := status.GetPosition().GetActualPosition()
	fmt.Printf("Current position: X=%.4f Y=%.4f Z=%.4f\n", position.GetX(), position.GetY(), position.GetZ())

	if skipDemo {
		fmt.Println("Skipping demo movements (--skip-demo)")
		return nil
	}

	if err := prepareForAxisJog(interrupt, client); err != nil {
		return err
	}
	if err := demoIncrementalJog(interrupt, client); err != nil {
		return err
	}
	if err := demoContinuousJog(interrupt, client); err != nil {
		return err
	}

	fmt.Println("\nJog demo complete!")
	return nil
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	skipDemo := flag.Bool("skip-demo", false, "Skip demo movements (just show status)")
	flag.Parse()

	// run returns instead of exiting, so its deferred cleanup always happens.
	if err := run(*host, *port, *skipDemo); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		// A stop that failed is the more important fact: exit 1 for it even
		// when an interrupt is what led to the stop.
		var stopError *jogStopError
		if errors.Is(err, errInterrupted) && !errors.As(err, &stopError) {
			os.Exit(130)
		}
		os.Exit(1)
	}
}
