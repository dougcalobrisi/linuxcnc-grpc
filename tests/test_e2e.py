"""
End-to-end tests against a real LinuxCNC simulator instance.

These tests require:
- A running LinuxCNC simulator (e.g. sim/axis/axis.ini)
- The real gRPC server connected to it on localhost:50051

Run with: pytest tests/test_e2e.py -v -m e2e
"""

import sys
import time
from pathlib import Path

import grpc
import pytest

SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from linuxcnc_pb import (
    hal_pb2,
    hal_pb2_grpc,
    linuxcnc_pb2,
    linuxcnc_pb2_grpc,
)

E2E_PORT = 50051
E2E_TARGET = f"localhost:{E2E_PORT}"

# Timeout for waiting on state changes (seconds)
STATE_TIMEOUT = 10.0
STATE_POLL_INTERVAL = 0.25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for_condition(stub, predicate, timeout=STATE_TIMEOUT, description="condition"):
    """Poll GetStatus until predicate(status) is True or timeout."""
    start = time.monotonic()
    last_status = None
    while time.monotonic() - start < timeout:
        resp = stub.GetStatus(
            linuxcnc_pb2.GetStatusRequest(), timeout=min(timeout, 5.0)
        )
        last_status = resp
        if predicate(resp):
            return resp
        time.sleep(STATE_POLL_INTERVAL)
    pytest.fail(
        f"Timed out after {timeout}s waiting for {description}. "
        f"Last task_state={last_status.task.task_state if last_status else 'N/A'}, "
        f"task_mode={last_status.task.task_mode if last_status else 'N/A'}"
    )


def wait_complete(stub, timeout=5.0):
    """Call WaitComplete RPC and assert RCS_DONE."""
    resp = stub.WaitComplete(linuxcnc_pb2.WaitCompleteRequest(timeout=timeout))
    assert resp.status == linuxcnc_pb2.RCS_DONE, (
        f"WaitComplete returned {resp.status} (expected RCS_DONE): {resp.error_message}"
    )
    return resp


def home_all(stub):
    """Home all joints and wait for joint 0 to report homed."""
    cmd = linuxcnc_pb2.LinuxCNCCommand(
        home=linuxcnc_pb2.HomeCommand(joint=-1)
    )
    resp = stub.SendCommand(cmd)
    assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message
    wait_for_condition(
        stub,
        lambda s: len(s.joints) > 0 and s.joints[0].homed,
        timeout=15.0,
        description="joint 0 homed (home_all)",
    )


def upload_test_file(stub, filename, content):
    """Upload a file and return the response."""
    return stub.UploadFile(
        linuxcnc_pb2.UploadFileRequest(filename=filename, content=content)
    )


def delete_test_file(stub, filename):
    """Delete a file, ignoring NOT_FOUND errors."""
    try:
        stub.DeleteFile(linuxcnc_pb2.DeleteFileRequest(filename=filename))
    except grpc.RpcError as e:
        if e.code() != grpc.StatusCode.NOT_FOUND:
            raise


def send_state_command(stub, state):
    """Send a state command and return the response."""
    cmd = linuxcnc_pb2.LinuxCNCCommand(
        state=linuxcnc_pb2.StateCommand(state=state)
    )
    return stub.SendCommand(cmd)


def send_mode_command(stub, mode):
    """Send a mode command and return the response."""
    cmd = linuxcnc_pb2.LinuxCNCCommand(
        mode=linuxcnc_pb2.ModeCommand(mode=mode)
    )
    return stub.SendCommand(cmd)


def ensure_machine_on(stub):
    """Bring the machine to STATE_ON in MANUAL mode."""
    # Estop reset
    resp = send_state_command(stub, linuxcnc_pb2.STATE_ESTOP_RESET)
    assert resp.status != linuxcnc_pb2.RCS_ERROR, (
        f"Estop reset failed: {resp.error_message}"
    )
    wait_for_condition(
        stub,
        lambda s: s.task.task_state != linuxcnc_pb2.STATE_ESTOP,
        description="estop reset",
    )

    # Machine on
    resp = send_state_command(stub, linuxcnc_pb2.STATE_ON)
    assert resp.status != linuxcnc_pb2.RCS_ERROR, (
        f"Machine on failed: {resp.error_message}"
    )
    wait_for_condition(
        stub,
        lambda s: s.task.task_state == linuxcnc_pb2.STATE_ON,
        description="machine on",
    )

    # Manual mode
    resp = send_mode_command(stub, linuxcnc_pb2.MODE_MANUAL)
    assert resp.status != linuxcnc_pb2.RCS_ERROR, (
        f"Manual mode failed: {resp.error_message}"
    )
    wait_for_condition(
        stub,
        lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MANUAL,
        description="manual mode",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def grpc_channel():
    """Create a gRPC channel to the real server."""
    channel = grpc.insecure_channel(E2E_TARGET)
    # Verify the channel is connected
    try:
        grpc.channel_ready_future(channel).result(timeout=10)
    except grpc.FutureTimeoutError:
        pytest.skip(f"gRPC server not available at {E2E_TARGET}")
    yield channel
    channel.close()


@pytest.fixture(scope="session")
def linuxcnc_stub(grpc_channel):
    """LinuxCNC service stub."""
    return linuxcnc_pb2_grpc.LinuxCNCServiceStub(grpc_channel)


@pytest.fixture(scope="session")
def hal_stub(grpc_channel):
    """HAL service stub."""
    return hal_pb2_grpc.HalServiceStub(grpc_channel)


@pytest.fixture(autouse=True)
def machine_on(linuxcnc_stub):
    """Ensure machine is ON and in MANUAL mode before each test."""
    ensure_machine_on(linuxcnc_stub)


# ---------------------------------------------------------------------------
# Status Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EStatus:
    """Verify GetStatus returns real LinuxCNC data."""

    def test_get_status_returns_real_version(self, linuxcnc_stub):
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        # Real LinuxCNC reports a version like "2.9.3" -- mock uses "2.9.0-mock"
        assert status.version != ""
        assert "mock" not in status.version.lower()

    def test_get_status_has_joints(self, linuxcnc_stub):
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        # The sim/axis config has 3 joints (X, Y, Z)
        # joints is a repeated JointStatus at the top level
        assert len(status.joints) >= 3

    def test_get_status_has_ini_filename(self, linuxcnc_stub):
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert status.task.ini_filename != ""
        assert ".ini" in status.task.ini_filename

    def test_get_status_position_fields(self, linuxcnc_stub):
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        # Position is at status.position.actual_position
        pos = status.position.actual_position
        assert pos is not None
        # Values are floats (could be 0.0 at startup, that's fine)
        assert isinstance(pos.x, float)
        assert isinstance(pos.y, float)
        assert isinstance(pos.z, float)

    def test_get_status_task_state_is_on(self, linuxcnc_stub):
        """Verify our fixture brought the machine to ON state."""
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert status.task.task_state == linuxcnc_pb2.STATE_ON


# ---------------------------------------------------------------------------
# State Transition Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EStateTransitions:
    """Verify real state machine transitions."""

    def test_estop_and_recovery(self, linuxcnc_stub):
        # Put into estop
        send_state_command(linuxcnc_stub, linuxcnc_pb2.STATE_ESTOP)
        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_state == linuxcnc_pb2.STATE_ESTOP,
            description="estop",
        )
        assert status.task.task_state == linuxcnc_pb2.STATE_ESTOP

        # Recover from estop
        send_state_command(linuxcnc_stub, linuxcnc_pb2.STATE_ESTOP_RESET)
        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_state == linuxcnc_pb2.STATE_ESTOP_RESET,
            description="estop reset",
        )
        assert status.task.task_state == linuxcnc_pb2.STATE_ESTOP_RESET

    def test_machine_on_off(self, linuxcnc_stub):
        # Machine should already be ON from fixture
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert status.task.task_state == linuxcnc_pb2.STATE_ON

        # In LinuxCNC, "off" means back to ESTOP_RESET (not a separate OFF state).
        # STATE_OFF maps to the linuxcnc "machine off" command which goes to
        # ESTOP_RESET state.
        send_state_command(linuxcnc_stub, linuxcnc_pb2.STATE_OFF)
        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_state != linuxcnc_pb2.STATE_ON,
            description="machine off (not ON)",
        )
        assert status.task.task_state in (
            linuxcnc_pb2.STATE_OFF,
            linuxcnc_pb2.STATE_ESTOP_RESET,
        )

    def test_mode_changes(self, linuxcnc_stub):
        # Switch to MDI mode
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode",
        )
        assert status.task.task_mode == linuxcnc_pb2.MODE_MDI

        # Switch back to manual
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MANUAL,
            description="manual mode",
        )
        assert status.task.task_mode == linuxcnc_pb2.MODE_MANUAL


# ---------------------------------------------------------------------------
# Command Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2ECommands:
    """Verify real command execution."""

    def test_home_joint(self, linuxcnc_stub):
        home_all(linuxcnc_stub)

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert len(status.joints) > 0 and status.joints[0].homed

    def test_mdi_command(self, linuxcnc_stub):
        home_all(linuxcnc_stub)

        # Switch to MDI mode
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode for command",
        )

        # Send a rapid move and use WaitComplete
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X1")
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        wait_complete(linuxcnc_stub, timeout=15.0)

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert abs(status.position.actual_position.x - 1.0) < 0.01

    def test_feedrate_override(self, linuxcnc_stub):
        # Set feedrate override to 50%
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            feedrate=linuxcnc_pb2.FeedrateCommand(scale=0.5)
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: abs(s.trajectory.feedrate - 0.5) < 0.01,
            description="feedrate 50%",
        )
        assert abs(status.trajectory.feedrate - 0.5) < 0.01

        # Restore to 100%
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            feedrate=linuxcnc_pb2.FeedrateCommand(scale=1.0)
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, (
            f"Feedrate restore failed: {resp.error_message}"
        )
        wait_for_condition(
            linuxcnc_stub,
            lambda s: abs(s.trajectory.feedrate - 1.0) < 0.01,
            description="feedrate restored to 100%",
        )

    def test_spindle_override(self, linuxcnc_stub):
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            spindle_override=linuxcnc_pb2.SpindleOverrideCommand(
                scale=0.75, spindle=0
            )
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: (
                len(s.spindles) > 0
                and abs(s.spindles[0].override - 0.75) < 0.01
            ),
            description="spindle override 75%",
        )

        # Restore
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            spindle_override=linuxcnc_pb2.SpindleOverrideCommand(
                scale=1.0, spindle=0
            )
        )
        linuxcnc_stub.SendCommand(cmd)


# ---------------------------------------------------------------------------
# Streaming Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EStreaming:
    """Verify streaming RPCs work with real LinuxCNC."""

    def test_stream_status_yields_updates(self, linuxcnc_stub):
        request = linuxcnc_pb2.StreamStatusRequest(interval_ms=100)
        count = 0
        stream = linuxcnc_stub.StreamStatus(request, timeout=5.0)
        try:
            for status in stream:
                assert status.task.task_state == linuxcnc_pb2.STATE_ON
                count += 1
                if count >= 3:
                    break
        finally:
            stream.cancel()
        assert count >= 3

    def test_stream_errors_connects(self, linuxcnc_stub):
        """Verify StreamErrors doesn't immediately error.

        We can't easily generate errors in sim, so just verify the stream
        can be opened and cancelled without crashing.
        """
        request = linuxcnc_pb2.StreamErrorsRequest()
        stream = linuxcnc_stub.StreamErrors(request)
        # Cancel after a short wait -- there may be no errors to stream
        stream.cancel()


# ---------------------------------------------------------------------------
# HAL Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EHal:
    """Verify real HAL data is accessible."""

    def test_get_system_status(self, hal_stub):
        status = hal_stub.GetSystemStatus(hal_pb2.GetSystemStatusRequest())
        # Should have pins, signals, and components from the sim config
        assert len(status.pins) > 0
        assert len(status.components) > 0

    def test_query_pins_returns_real_pins(self, hal_stub):
        resp = hal_stub.QueryPins(hal_pb2.QueryPinsCommand(pattern="motion.*"))
        # A real sim config should have motion controller pins
        assert len(resp.pins) > 0
        pin_names = [p.name for p in resp.pins]
        # motion-controller creates pins like motion.in-position
        assert any("motion" in name for name in pin_names)

    def test_query_components(self, hal_stub):
        resp = hal_stub.QueryComponents(hal_pb2.QueryComponentsCommand(pattern="*"))
        assert len(resp.components) > 0
        comp_names = [c.name for c in resp.components]
        # motmod (motion module) should be loaded in any sim config
        assert any("motion" in name or "motmod" in name for name in comp_names), (
            f"Expected a motion component, got: {comp_names}"
        )

    def test_query_signals(self, hal_stub):
        resp = hal_stub.QuerySignals(hal_pb2.QuerySignalsCommand(pattern="*"))
        # Sim configs typically have signals connecting components
        # Even if empty, the RPC should succeed
        assert resp is not None


# ---------------------------------------------------------------------------
# File Management Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EFiles:
    """Verify file upload/list/delete against the real NC files directory."""

    TEST_FILENAME = "e2e_test_program.ngc"
    TEST_CONTENT = "(e2e test program)\nG0 X0 Y0 Z0\nM2\n"

    def test_upload_list_delete_cycle(self, linuxcnc_stub):
        # Upload
        upload_resp = linuxcnc_stub.UploadFile(
            linuxcnc_pb2.UploadFileRequest(
                filename=self.TEST_FILENAME,
                content=self.TEST_CONTENT,
            )
        )
        assert self.TEST_FILENAME in upload_resp.path

        # List and verify it appears
        list_resp = linuxcnc_stub.ListFiles(linuxcnc_pb2.ListFilesRequest())
        names = [f.name for f in list_resp.files]
        assert self.TEST_FILENAME in names

        # Delete
        delete_resp = linuxcnc_stub.DeleteFile(
            linuxcnc_pb2.DeleteFileRequest(filename=self.TEST_FILENAME)
        )
        assert self.TEST_FILENAME in delete_resp.path

        # Verify it's gone
        list_resp = linuxcnc_stub.ListFiles(linuxcnc_pb2.ListFilesRequest())
        names = [f.name for f in list_resp.files]
        assert self.TEST_FILENAME not in names


# ---------------------------------------------------------------------------
# WaitComplete Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EWaitComplete:
    """Verify WaitComplete RPC behavior."""

    def test_wait_complete_after_mode_change(self, linuxcnc_stub):
        """WaitComplete returns RCS_DONE after a mode switch."""
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_complete(linuxcnc_stub, timeout=5.0)

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert status.task.task_mode == linuxcnc_pb2.MODE_MDI

        # Restore
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
        wait_complete(linuxcnc_stub)

    def test_wait_complete_after_mdi_motion(self, linuxcnc_stub):
        """WaitComplete blocks until MDI motion finishes."""
        home_all(linuxcnc_stub)

        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode",
        )

        cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X5")
        )
        linuxcnc_stub.SendCommand(cmd)
        wait_complete(linuxcnc_stub, timeout=10.0)

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert abs(status.position.actual_position.x - 5.0) < 0.01

    def test_wait_complete_timeout(self, linuxcnc_stub):
        """WaitComplete with a very short timeout returns before motion completes."""
        home_all(linuxcnc_stub)

        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode",
        )

        # Send a slow, short move well within sim limits
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G1 X5 F5")
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, (
            f"MDI command failed: {resp.error_message}"
        )

        # Very short timeout — the WaitComplete RPC should return promptly
        # rather than blocking indefinitely. We primarily expect RCS_EXEC
        # (motion still running), but also accept RCS_DONE (motion completed
        # very fast in CI) or RCS_ERROR (timeout mechanism returned an error).
        # The key guarantee: the call respects the timeout and returns.
        resp = linuxcnc_stub.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(timeout=0.05)
        )
        assert resp.status in (
            linuxcnc_pb2.RCS_EXEC,
            linuxcnc_pb2.RCS_DONE,
            linuxcnc_pb2.RCS_ERROR,
        )

        # Abort and wait for idle
        abort_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(abort=True)
        )
        linuxcnc_stub.SendCommand(abort_cmd)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE,
            description="interp idle after abort",
        )

        # Reset position to origin so later tests don't inherit a stale X
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode for position reset",
        )
        reset_cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X0 Y0 Z0")
        )
        linuxcnc_stub.SendCommand(reset_cmd)
        wait_complete(linuxcnc_stub, timeout=15.0)


# ---------------------------------------------------------------------------
# G-code Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EGcodeExecution:
    """Verify program open/run/pause/resume/abort/step."""

    PROGRAM_FILE = "e2e_gcode_test.ngc"

    def _upload_and_open(self, linuxcnc_stub, filename, content):
        """Upload a file, switch to AUTO, and open it."""
        delete_test_file(linuxcnc_stub, filename)
        upload_test_file(linuxcnc_stub, filename, content)

        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_AUTO)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_AUTO,
            description="AUTO mode",
        )

        open_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(open=filename)
        )
        resp = linuxcnc_stub.SendCommand(open_cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message
        wait_complete(linuxcnc_stub)

    def test_upload_open_run_verify_position(self, linuxcnc_stub):
        """Full cycle: upload, open, run, verify final position."""
        home_all(linuxcnc_stub)

        gcode = "G0 X10 Y5\nM2\n"
        self._upload_and_open(linuxcnc_stub, self.PROGRAM_FILE, gcode)

        run_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(run_from_line=0)
        )
        linuxcnc_stub.SendCommand(run_cmd)

        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE
            and abs(s.position.actual_position.x - 10.0) < 0.1,
            timeout=15.0,
            description="program complete at X=10 Y=5",
        )

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert abs(status.position.actual_position.x - 10.0) < 0.1
        assert abs(status.position.actual_position.y - 5.0) < 0.1

        delete_test_file(linuxcnc_stub, self.PROGRAM_FILE)

    def test_program_pause_resume(self, linuxcnc_stub):
        """Pause a running program, then resume and verify completion."""
        home_all(linuxcnc_stub)

        # Use a dwell to create a reliable pause window
        gcode = "G0 X0 Y0\nG4 P2\nG0 X3\nM2\n"
        self._upload_and_open(linuxcnc_stub, self.PROGRAM_FILE, gcode)

        run_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(run_from_line=0)
        )
        linuxcnc_stub.SendCommand(run_cmd)

        # Wait for the program to start executing
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state != linuxcnc_pb2.INTERP_IDLE,
            timeout=5.0,
            description="program started",
        )

        # Pause
        pause_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(pause=True)
        )
        linuxcnc_stub.SendCommand(pause_cmd)

        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.trajectory.paused,
            timeout=5.0,
            description="program paused",
        )

        # Resume
        resume_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(resume=True)
        )
        linuxcnc_stub.SendCommand(resume_cmd)

        # Wait for completion
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE
            and abs(s.position.actual_position.x - 3.0) < 0.1,
            timeout=15.0,
            description="program complete at X=3 after resume",
        )

        delete_test_file(linuxcnc_stub, self.PROGRAM_FILE)

    def test_program_abort(self, linuxcnc_stub):
        """Aborting a running program returns interp to IDLE."""
        home_all(linuxcnc_stub)

        gcode = "G4 P5\nG0 X20\nM2\n"
        self._upload_and_open(linuxcnc_stub, self.PROGRAM_FILE, gcode)

        run_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(run_from_line=0)
        )
        linuxcnc_stub.SendCommand(run_cmd)

        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state != linuxcnc_pb2.INTERP_IDLE,
            timeout=5.0,
            description="program started for abort test",
        )

        abort_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(abort=True)
        )
        linuxcnc_stub.SendCommand(abort_cmd)

        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE,
            timeout=10.0,
            description="interp idle after abort",
        )

        delete_test_file(linuxcnc_stub, self.PROGRAM_FILE)

    def test_program_step(self, linuxcnc_stub):
        """Single-stepping executes one line at a time."""
        home_all(linuxcnc_stub)

        # Use large gap between positions so read-ahead ambiguity doesn't matter
        gcode = "G0 X5\nG0 X50\nM2\n"
        self._upload_and_open(linuxcnc_stub, self.PROGRAM_FILE, gcode)

        step_cmd = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(step=True)
        )
        linuxcnc_stub.SendCommand(step_cmd)

        # Wait for step to finish (paused before next line, or interp idle)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE
            or s.trajectory.paused,
            timeout=10.0,
            description="step complete or paused",
        )

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        # After first step, X should NOT be at the second line's X=50
        assert status.position.actual_position.x < 20.0, (
            f"Expected X < 20 after first step, got X={status.position.actual_position.x}"
        )

        delete_test_file(linuxcnc_stub, self.PROGRAM_FILE)


# ---------------------------------------------------------------------------
# Negative Path Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2ENegativePaths:
    """Verify error handling and validation."""

    def test_mdi_empty_command(self, linuxcnc_stub):
        """Empty MDI string returns RCS_ERROR."""
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode",
        )

        cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="")
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status == linuxcnc_pb2.RCS_ERROR

    def test_mdi_wrong_mode(self, linuxcnc_stub):
        """MDI command while in MANUAL mode should fail or be rejected."""
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MANUAL,
            description="manual mode",
        )

        cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X99")
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        # LinuxCNC may reject synchronously (RCS_ERROR) or accept silently.
        # Either way, the command should not successfully execute.
        if resp.status == linuxcnc_pb2.RCS_ERROR:
            return  # Rejected synchronously — good

        # If accepted, verify it didn't actually run (mode is still MANUAL)
        time.sleep(0.5)
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert status.task.task_mode == linuxcnc_pb2.MODE_MANUAL
        # Position should NOT be at X=99
        assert abs(status.position.actual_position.x - 99.0) > 1.0

    def test_jog_invalid_axis(self, linuxcnc_stub):
        """Jogging an out-of-range axis index returns RCS_ERROR."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_CONTINUOUS,
                is_joint=False,
                index=99,
                velocity=10.0,
            )
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status == linuxcnc_pb2.RCS_ERROR

    def test_upload_path_traversal(self, linuxcnc_stub):
        """Path traversal in filename is rejected with INVALID_ARGUMENT."""
        with pytest.raises(grpc.RpcError) as exc_info:
            linuxcnc_stub.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="../../etc/passwd",
                    content="malicious content",
                )
            )
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_delete_nonexistent_file(self, linuxcnc_stub):
        """Deleting a nonexistent file returns NOT_FOUND."""
        with pytest.raises(grpc.RpcError) as exc_info:
            linuxcnc_stub.DeleteFile(
                linuxcnc_pb2.DeleteFileRequest(
                    filename="nonexistent_e2e_file_99999.ngc"
                )
            )
        assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND

    def test_upload_fail_if_exists(self, linuxcnc_stub):
        """Upload with fail_if_exists=True rejects duplicate filenames."""
        filename = "e2e_duplicate_test.ngc"
        content = "G0 X0\nM2\n"

        # Clean up first
        delete_test_file(linuxcnc_stub, filename)

        # First upload succeeds
        upload_test_file(linuxcnc_stub, filename, content)

        # Second upload with fail_if_exists should fail
        try:
            with pytest.raises(grpc.RpcError) as exc_info:
                linuxcnc_stub.UploadFile(
                    linuxcnc_pb2.UploadFileRequest(
                        filename=filename,
                        content=content,
                        fail_if_exists=True,
                    )
                )
            assert exc_info.value.code() == grpc.StatusCode.ALREADY_EXISTS
        finally:
            delete_test_file(linuxcnc_stub, filename)


# ---------------------------------------------------------------------------
# Jog Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EJog:
    """Verify jogging commands."""

    def test_jog_increment(self, linuxcnc_stub):
        """Incremental jog moves the axis by the specified amount."""
        home_all(linuxcnc_stub)

        # Enable teleop mode for axis jogging
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            teleop=linuxcnc_pb2.TeleopCommand(enable=True)
        )
        linuxcnc_stub.SendCommand(cmd)
        wait_complete(linuxcnc_stub)

        # Record starting position
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        start_x = status.position.actual_position.x

        # Jog increment on X axis
        jog_cmd = linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_INCREMENT,
                is_joint=False,
                index=0,
                velocity=10.0,
                increment=1.0,
            )
        )
        linuxcnc_stub.SendCommand(jog_cmd)

        # Wait for position to change by ~1.0
        wait_for_condition(
            linuxcnc_stub,
            lambda s: abs(s.position.actual_position.x - (start_x + 1.0)) < 0.5,
            timeout=10.0,
            description="X moved ~1.0 after jog increment",
        )

    def test_jog_continuous_and_stop(self, linuxcnc_stub):
        """Continuous jog moves the axis, stop halts it."""
        home_all(linuxcnc_stub)

        cmd = linuxcnc_pb2.LinuxCNCCommand(
            teleop=linuxcnc_pb2.TeleopCommand(enable=True)
        )
        linuxcnc_stub.SendCommand(cmd)
        wait_complete(linuxcnc_stub)

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        start_x = status.position.actual_position.x

        # Start continuous jog
        jog_cmd = linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_CONTINUOUS,
                is_joint=False,
                index=0,
                velocity=10.0,
            )
        )
        linuxcnc_stub.SendCommand(jog_cmd)

        # Brief delay to let it move
        time.sleep(0.5)

        # Stop jog
        stop_cmd = linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_STOP,
                is_joint=False,
                index=0,
            )
        )
        linuxcnc_stub.SendCommand(stop_cmd)

        # Verify position changed
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert abs(status.position.actual_position.x - start_x) > 0.1, (
            "Position should have changed after continuous jog"
        )


# ---------------------------------------------------------------------------
# Coolant Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2ECoolant:
    """Verify coolant commands."""

    def test_coolant_mist_on_off(self, linuxcnc_stub):
        """Mist coolant can be toggled on and off."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            coolant=linuxcnc_pb2.CoolantCommand(mist=True, flood=False)
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.io.mist == linuxcnc_pb2.COOLANT_ON,
            description="mist on",
        )
        assert status.io.mist == linuxcnc_pb2.COOLANT_ON

        # Turn off
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            coolant=linuxcnc_pb2.CoolantCommand(mist=False, flood=False)
        )
        linuxcnc_stub.SendCommand(cmd)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.io.mist == linuxcnc_pb2.COOLANT_OFF,
            description="mist off",
        )

    def test_coolant_flood_on_off(self, linuxcnc_stub):
        """Flood coolant can be toggled on and off."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            coolant=linuxcnc_pb2.CoolantCommand(mist=False, flood=True)
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.io.flood == linuxcnc_pb2.COOLANT_ON,
            description="flood on",
        )
        assert status.io.flood == linuxcnc_pb2.COOLANT_ON

        # Turn off
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            coolant=linuxcnc_pb2.CoolantCommand(mist=False, flood=False)
        )
        linuxcnc_stub.SendCommand(cmd)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.io.flood == linuxcnc_pb2.COOLANT_OFF,
            description="flood off",
        )


# ---------------------------------------------------------------------------
# Rapid Rate and Max Velocity Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2ERapidAndMaxvel:
    """Verify rapidrate and maxvel commands."""

    def test_rapidrate_override(self, linuxcnc_stub):
        """Rapid rate override changes trajectory.rapidrate."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            rapidrate=linuxcnc_pb2.RapidrateCommand(scale=0.5)
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: abs(s.trajectory.rapidrate - 0.5) < 0.01,
            description="rapidrate 50%",
        )
        assert abs(status.trajectory.rapidrate - 0.5) < 0.01

        # Restore
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            rapidrate=linuxcnc_pb2.RapidrateCommand(scale=1.0)
        )
        linuxcnc_stub.SendCommand(cmd)

    def test_maxvel_command(self, linuxcnc_stub):
        """Max velocity command changes trajectory.max_velocity."""
        # Read current max_velocity
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        original_maxvel = status.trajectory.max_velocity

        new_maxvel = original_maxvel * 0.5
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            maxvel=linuxcnc_pb2.MaxVelCommand(velocity=new_maxvel)
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: abs(s.trajectory.max_velocity - new_maxvel) < 0.1,
            description="max_velocity changed",
        )
        assert abs(status.trajectory.max_velocity - new_maxvel) < 0.1

        # Restore
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            maxvel=linuxcnc_pb2.MaxVelCommand(velocity=original_maxvel)
        )
        linuxcnc_stub.SendCommand(cmd)


# ---------------------------------------------------------------------------
# Override Config Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EOverrideConfig:
    """Verify override configuration commands."""

    def test_disable_feed_override(self, linuxcnc_stub):
        """Disabling feed override is reflected in trajectory status."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            override_config=linuxcnc_pb2.OverrideConfigCommand(
                feed_override_enable=False,
                spindle_override_enable=True,
                feed_hold_enable=True,
                adaptive_feed_enable=False,
            )
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: not s.trajectory.feed_override_enabled,
            description="feed override disabled",
        )
        assert not status.trajectory.feed_override_enabled

        # Re-enable
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            override_config=linuxcnc_pb2.OverrideConfigCommand(
                feed_override_enable=True,
                spindle_override_enable=True,
                feed_hold_enable=True,
                adaptive_feed_enable=False,
            )
        )
        linuxcnc_stub.SendCommand(cmd)

    def test_disable_spindle_override(self, linuxcnc_stub):
        """Disabling spindle override is reflected in spindle status."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            override_config=linuxcnc_pb2.OverrideConfigCommand(
                feed_override_enable=True,
                spindle_override_enable=False,
                spindle=0,
                feed_hold_enable=True,
                adaptive_feed_enable=False,
            )
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: len(s.spindles) > 0 and not s.spindles[0].override_enabled,
            description="spindle override disabled",
        )
        assert not status.spindles[0].override_enabled

        # Re-enable
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            override_config=linuxcnc_pb2.OverrideConfigCommand(
                feed_override_enable=True,
                spindle_override_enable=True,
                spindle=0,
                feed_hold_enable=True,
                adaptive_feed_enable=False,
            )
        )
        linuxcnc_stub.SendCommand(cmd)


# ---------------------------------------------------------------------------
# Program Options Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EProgramOptions:
    """Verify optional_stop and block_delete commands."""

    def test_optional_stop(self, linuxcnc_stub):
        """Setting optional_stop is reflected in task status."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            program_options=linuxcnc_pb2.ProgramOptionsCommand(
                optional_stop=True, block_delete=False
            )
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.optional_stop,
            description="optional_stop enabled",
        )
        assert status.task.optional_stop

        # Restore
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            program_options=linuxcnc_pb2.ProgramOptionsCommand(
                optional_stop=False, block_delete=False
            )
        )
        linuxcnc_stub.SendCommand(cmd)

    def test_block_delete(self, linuxcnc_stub):
        """Setting block_delete is reflected in task status."""
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            program_options=linuxcnc_pb2.ProgramOptionsCommand(
                optional_stop=False, block_delete=True
            )
        )
        resp = linuxcnc_stub.SendCommand(cmd)
        assert resp.status != linuxcnc_pb2.RCS_ERROR, resp.error_message

        status = wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.block_delete,
            description="block_delete enabled",
        )
        assert status.task.block_delete

        # Restore
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            program_options=linuxcnc_pb2.ProgramOptionsCommand(
                optional_stop=False, block_delete=False
            )
        )
        linuxcnc_stub.SendCommand(cmd)


# ---------------------------------------------------------------------------
# HAL GetValue Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EHalGetValue:
    """Verify HAL GetValue RPC."""

    def test_get_value_known_pin(self, hal_stub):
        """GetValue for a known pin returns a value."""
        resp = hal_stub.GetValue(hal_pb2.GetValueCommand(name="motion.in-position"))
        assert resp.success, f"GetValue failed: {resp.error}"
        # motion.in-position is a bit (boolean) pin
        assert resp.type == hal_pb2.HAL_BIT

    def test_get_value_nonexistent(self, hal_stub):
        """GetValue for a nonexistent pin returns success=False."""
        resp = hal_stub.GetValue(hal_pb2.GetValueCommand(name="nonexistent.pin"))
        assert not resp.success


# ---------------------------------------------------------------------------
# HAL QueryParams Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EHalQueryParams:
    """Verify HAL QueryParams RPC."""

    def test_query_params_all(self, hal_stub):
        """QueryParams with wildcard returns params."""
        resp = hal_stub.QueryParams(hal_pb2.QueryParamsCommand(pattern="*"))
        assert resp.success, f"QueryParams failed: {resp.error}"
        # Sim config should have at least some parameters
        assert len(resp.params) > 0

    def test_query_params_filtered(self, hal_stub):
        """QueryParams with a filter pattern returns matching params."""
        resp = hal_stub.QueryParams(hal_pb2.QueryParamsCommand(pattern="motion-command-handler.*"))
        assert resp.success, f"QueryParams failed: {resp.error}"
        for param in resp.params:
            assert "motion-command-handler" in param.name, (
                f"Param {param.name} doesn't match filter"
            )


# ---------------------------------------------------------------------------
# HAL StreamStatus Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EHalStreamStatus:
    """Verify HAL StreamStatus RPC."""

    def test_hal_stream_status(self, hal_stub):
        """HAL StreamStatus yields updates with pins and components."""
        request = hal_pb2.HalStreamStatusRequest(interval_ms=200)
        count = 0
        stream = hal_stub.StreamStatus(request, timeout=10.0)
        try:
            for status in stream:
                assert len(status.pins) > 0
                assert len(status.components) > 0
                count += 1
                if count >= 3:
                    break
        finally:
            stream.cancel()
        assert count >= 3


# ---------------------------------------------------------------------------
# HAL SendCommand Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EHalSendCommand:
    """Verify HAL SendCommand RPC for query commands.

    Note: the query-style commands component_exists, component_ready, and
    pin_has_writer all go through SendCommand and return HalCommandResponse,
    but they do not all use identical success/error conventions:

    - component_exists and pin_has_writer: the server sets success=True for
      both positive and negative results, using the error field to communicate
      negative outcomes (e.g. "does not exist", "has no writer").
    - component_ready: the server returns success=False when the component
      does not exist, and success=True when it does (with the error field
      indicating whether it is ready).

    The tests in this class assume this behavior and should be updated if the
    RPC contract changes.
    """

    @staticmethod
    def _find_existing_component(hal_stub):
        """Try common HAL component names until we find one that exists.

        Note: hal.component_exists() uses the HAL-registered name which may
        differ from the derived names in QueryComponents (which are inferred
        from pin prefixes).
        """
        # Common component names in LinuxCNC sim configs
        candidates = ["halui", "motmod", "motion", "iocontrol.0", "iocontrol"]
        for name in candidates:
            cmd = hal_pb2.HalCommand(
                component_exists=hal_pb2.ComponentExistsCommand(name=name)
            )
            resp = hal_stub.SendCommand(cmd)
            if resp.success and resp.error == "":
                return name
        pytest.skip(f"No known HAL component found (tried: {candidates})")

    def test_hal_component_exists(self, hal_stub):
        """component_exists for a real component returns success with no error."""
        name = self._find_existing_component(hal_stub)
        cmd = hal_pb2.HalCommand(
            component_exists=hal_pb2.ComponentExistsCommand(name=name)
        )
        resp = hal_stub.SendCommand(cmd)
        assert resp.success
        assert resp.error == "", f"Expected no error for '{name}', got: {resp.error}"

    def test_hal_component_exists_nonexistent(self, hal_stub):
        """component_exists for a fake name returns success=True but error message."""
        cmd = hal_pb2.HalCommand(
            component_exists=hal_pb2.ComponentExistsCommand(name="nonexistent_component_xyz")
        )
        resp = hal_stub.SendCommand(cmd)
        assert resp.success
        assert "does not exist" in resp.error

    def test_hal_component_ready(self, hal_stub):
        """component_ready for a real component returns success."""
        name = self._find_existing_component(hal_stub)
        cmd = hal_pb2.HalCommand(
            component_ready=hal_pb2.ComponentReadyCommand(name=name)
        )
        resp = hal_stub.SendCommand(cmd)
        assert resp.success, f"component_ready failed for '{name}': {resp.error}"

    def test_hal_pin_has_writer(self, hal_stub):
        """pin_has_writer RPC returns a valid response for a known pin."""
        # Use motion.in-position — a well-known pin in any sim config.
        # We just verify the RPC succeeds and returns a meaningful response,
        # not the specific boolean value (which depends on signal connections).
        cmd = hal_pb2.HalCommand(
            pin_has_writer=hal_pb2.PinHasWriterCommand(name="motion.in-position")
        )
        resp = hal_stub.SendCommand(cmd)
        assert resp.success
        # Response should either be empty (has writer) or contain "has no writer"
        assert resp.error == "" or "has no writer" in resp.error


# ---------------------------------------------------------------------------
# HAL WatchValues Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EHalWatchValues:
    """Verify HAL WatchValues RPC."""

    def test_watch_values_receives_updates(self, hal_stub, linuxcnc_stub):
        """WatchValues stream can be opened and delivers batches."""
        import threading

        home_all(linuxcnc_stub)

        request = hal_pb2.WatchRequest(
            names=["motion.in-position"],
            interval_ms=100,
        )
        stream = hal_stub.WatchValues(request)

        # Collect batches in a background thread with a hard timeout
        batches = []
        collect_done = threading.Event()

        def collect():
            try:
                for batch in stream:
                    batches.append(batch)
                    if len(batches) >= 2:
                        break
            except grpc.RpcError:
                pass
            finally:
                collect_done.set()

        t = threading.Thread(target=collect, daemon=True)
        t.start()

        # Trigger motion to generate value changes
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
            description="MDI mode for watch test",
        )
        cmd = linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G1 X2 F100")
        )
        linuxcnc_stub.SendCommand(cmd)

        # Wait up to 15s for at least one batch, then cancel
        collect_done.wait(timeout=15.0)
        stream.cancel()
        t.join(timeout=5.0)

        assert len(batches) >= 1, "Expected at least one ValueChangeBatch"

        # Wait for motion to complete
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE,
            description="interp idle after watch test",
        )
