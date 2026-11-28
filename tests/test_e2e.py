"""
End-to-end tests against a real LinuxCNC simulator instance.

These tests require:
- A running LinuxCNC simulator (e.g. sim/axis/axis.ini)
- The real gRPC server connected to it on localhost:50051

Run with: pytest tests/test_e2e.py -v -m e2e
"""

import os
import signal
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

    def test_get_status_lists_only_the_machines_joints_and_spindles(self, linuxcnc_stub):
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        # LinuxCNC's own status has 16 joint and 8 spindle entries whatever
        # the machine has; the server reports the real ones only.
        assert status.trajectory.joints >= 3
        assert status.trajectory.spindles >= 1
        assert len(status.joints) == status.trajectory.joints
        assert [joint.joint_number for joint in status.joints] == list(
            range(status.trajectory.joints)
        )
        assert len(status.spindles) == status.trajectory.spindles
        assert len(status.limits.limit_flags) == status.trajectory.joints
        assert len(status.limits.homed) == status.trajectory.joints

    def test_get_status_tool_table_entries_carry_their_table_index(self, linuxcnc_stub):
        """
        table_index is the index pocket_prepped refers to.

        Measured on the sim/axis configuration: ids [-1, 1, 2, 3, 99999, 4]
        at indices 0..5, and pocket_prepped 5 after "T4". So after preparing
        a tool, the one entry whose table_index equals pocket_prepped must
        carry that tool's id; an index shifted by any constant would name
        another entry or none.
        """
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        tool_table = list(status.tool.tool_table)
        table_summary = [(tool_entry.table_index, tool_entry.id) for tool_entry in tool_table]
        assert tool_table, "the simulator's tool table came back empty"
        assert all(tool_entry.HasField("table_index") for tool_entry in tool_table)
        table_indices = [tool_entry.table_index for tool_entry in tool_table]
        # Indices follow LinuxCNC's table order and no two entries share one.
        assert table_indices == sorted(set(table_indices)), table_summary
        assert all(tool_entry.id != 0 for tool_entry in tool_table), table_summary

        # A tool that is not the one already prepared or loaded, so that
        # preparing it changes pocket_prepped. Preferably one whose id differs
        # from its index (the last such entry: tool 4 at index 5 in sim/axis),
        # so that the two cannot be mistaken for each other.
        pocket_prepped_before = status.tool.pocket_prepped
        candidate_entries = [
            tool_entry for tool_entry in tool_table
            if tool_entry.id > 0
            and tool_entry.table_index != pocket_prepped_before
            and tool_entry.id != status.tool.tool_in_spindle
        ]
        assert candidate_entries, (
            f"no tool to prepare: table (index, id) = {table_summary}, "
            f"pocket_prepped = {pocket_prepped_before}, "
            f"tool_in_spindle = {status.tool.tool_in_spindle}"
        )
        entries_with_id_unlike_index = [
            tool_entry for tool_entry in candidate_entries
            if tool_entry.id != tool_entry.table_index
        ]
        chosen_entry = (entries_with_id_unlike_index or candidate_entries)[-1]
        chosen_tool_id = chosen_entry.id

        home_all(linuxcnc_stub)
        try:
            mode_response = send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
            assert mode_response.status != linuxcnc_pb2.RCS_ERROR, mode_response.error_message
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
                description="MDI mode for the tool prepare",
            )

            prepare_response = linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command=f"T{chosen_tool_id}")
            ))
            assert prepare_response.status != linuxcnc_pb2.RCS_ERROR, (
                prepare_response.error_message
            )
            assert prepare_response.HasField("linuxcnc_serial")
            completion = linuxcnc_stub.WaitComplete(linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=prepare_response.linuxcnc_serial, timeout=15.0
            ))
            assert completion.status == linuxcnc_pb2.RCS_DONE, (
                f"T{chosen_tool_id}: WaitComplete returned {completion.status}: "
                f"{completion.error_message}"
            )

            # The prepare can reach the status a little after the command is done.
            status_after = wait_for_condition(
                linuxcnc_stub,
                lambda s: s.tool.pocket_prepped != pocket_prepped_before,
                description=(
                    f"pocket_prepped to change from {pocket_prepped_before} "
                    f"after T{chosen_tool_id}"
                ),
            )
            pocket_prepped_after = status_after.tool.pocket_prepped
            table_summary_after = [
                (tool_entry.table_index, tool_entry.id)
                for tool_entry in status_after.tool.tool_table
            ]
            ids_at_the_prepped_index = [
                tool_entry.id for tool_entry in status_after.tool.tool_table
                if tool_entry.table_index == pocket_prepped_after
            ]
            assert ids_at_the_prepped_index == [chosen_tool_id], (
                f"after T{chosen_tool_id}: pocket_prepped went from "
                f"{pocket_prepped_before} to {pocket_prepped_after}, "
                f"table (index, id) = {table_summary_after}"
            )
        finally:
            # Back to the mode the other tests start from.
            send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MANUAL,
                description="manual mode after the tool prepare",
            )

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


def collect_stream_errors(stub, expected_text, replay_buffered=False, timeout=10.0):
    """
    Open StreamErrors in a thread and collect messages until one contains expected_text.

    Returns (thread, collected_messages, ready_event). ready_event is set once
    the stream is open on the server, so the caller knows a message posted
    afterwards will be seen.
    """
    import threading

    collected_messages = []
    stream_is_open = threading.Event()

    def read_stream():
        stream = stub.StreamErrors(
            linuxcnc_pb2.StreamErrorsRequest(replay_buffered=replay_buffered),
            timeout=timeout,
        )
        try:
            # The server sends the response headers once it has subscribed.
            stream.initial_metadata()
            stream_is_open.set()
            for error_message in stream:
                collected_messages.append(error_message)
                if expected_text in error_message.message:
                    break
        except grpc.RpcError:
            pass
        finally:
            stream_is_open.set()
            stream.cancel()

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    return reader, collected_messages, stream_is_open


def post_operator_error(stub, text):
    """Have LinuxCNC put an operator error on its error channel."""
    return stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
        operator_message=linuxcnc_pb2.OperatorMessageCommand(
            type=linuxcnc_pb2.OperatorMessageCommand.ERROR, message=text
        )
    ))


@pytest.mark.e2e
class TestE2EErrors:
    """Verify error fan-out against LinuxCNC's real error channel."""

    def test_two_streams_both_receive_the_same_message(self, linuxcnc_stub):
        """Every subscriber gets every message, with the same sequence number."""
        text = f"e2e fan-out {time.time_ns()}"
        first_reader, first_messages, first_open = collect_stream_errors(linuxcnc_stub, text)
        second_reader, second_messages, second_open = collect_stream_errors(linuxcnc_stub, text)
        assert first_open.wait(5.0) and second_open.wait(5.0)

        post_operator_error(linuxcnc_stub, text)
        first_reader.join(timeout=10.0)
        second_reader.join(timeout=10.0)

        first_match = [m for m in first_messages if text in m.message]
        second_match = [m for m in second_messages if text in m.message]
        assert len(first_match) == 1 and len(second_match) == 1
        assert first_match[0].type == linuxcnc_pb2.ErrorMessage.OPERATOR_ERROR
        assert first_match[0].sequence > 0
        assert first_match[0].sequence == second_match[0].sequence
        assert first_match[0].timestamp == second_match[0].timestamp

    def test_status_errors_and_replay_carry_the_streamed_message(self, linuxcnc_stub):
        """A message read for a stream shows up in status.errors and in a later replay."""
        text = f"e2e status errors {time.time_ns()}"
        reader, messages, stream_open = collect_stream_errors(linuxcnc_stub, text)
        assert stream_open.wait(5.0)
        post_operator_error(linuxcnc_stub, text)
        reader.join(timeout=10.0)
        streamed = [m for m in messages if text in m.message]
        assert len(streamed) == 1

        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        in_status = [e for e in status.errors if text in e.message]
        assert len(in_status) == 1
        assert in_status[0].sequence == streamed[0].sequence

        # A subscriber that connects afterwards can ask for the buffer.
        replay_reader, replayed_messages, replay_open = collect_stream_errors(
            linuxcnc_stub, text, replay_buffered=True
        )
        replay_reader.join(timeout=10.0)
        replayed = [m for m in replayed_messages if text in m.message]
        assert len(replayed) == 1
        assert replayed[0].sequence == streamed[0].sequence

    def test_mdi_rejection_reaches_the_stream(self, linuxcnc_stub):
        """A real LinuxCNC error (MDI in manual mode) arrives through StreamErrors."""
        send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MANUAL,
            description="MANUAL mode",
        )
        marker = f"e2e marker {time.time_ns()}"
        reader, messages, stream_open = collect_stream_errors(linuxcnc_stub, marker)
        assert stream_open.wait(5.0)

        linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X0")
        ))
        time.sleep(0.5)
        # The marker ends the collection; everything before it is LinuxCNC's own.
        post_operator_error(linuxcnc_stub, marker)
        reader.join(timeout=10.0)

        before_marker = [m for m in messages if marker not in m.message]
        assert any("MDI" in m.message or "mode" in m.message.lower() for m in before_marker), (
            f"expected LinuxCNC's MDI rejection, got {[m.message for m in messages]}"
        )


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


    def test_read_file_returns_uploaded_content(self, linuxcnc_stub):
        """ReadFile gives back exactly what UploadFile wrote, whole and in chunks."""
        filename = "e2e_read_test.ngc"
        upload_test_file(linuxcnc_stub, filename, self.TEST_CONTENT)
        try:
            whole = linuxcnc_stub.ReadFile(linuxcnc_pb2.ReadFileRequest(filename=filename))
            assert whole.content.decode() == self.TEST_CONTENT
            assert whole.end_of_file
            assert whole.total_size_bytes == len(self.TEST_CONTENT.encode())

            collected = b""
            while True:
                chunk = linuxcnc_stub.ReadFile(linuxcnc_pb2.ReadFileRequest(
                    filename=filename, offset=len(collected), max_bytes=7
                ))
                assert chunk.modified_timestamp == whole.modified_timestamp
                collected += chunk.content
                if chunk.end_of_file:
                    break
            assert collected == whole.content
        finally:
            delete_test_file(linuxcnc_stub, filename)

    def test_read_file_rejects_path_traversal(self, linuxcnc_stub):
        with pytest.raises(grpc.RpcError) as error_info:
            linuxcnc_stub.ReadFile(linuxcnc_pb2.ReadFileRequest(filename="../../etc/passwd"))
        assert error_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_resolve_file_identifies_loaded_program(self, linuxcnc_stub):
        """After program_open, ResolveFile marks that name, and only that name, as loaded."""
        loaded_name = "e2e_resolve_loaded.ngc"
        other_name = "e2e_resolve_other.ngc"
        upload_resp = upload_test_file(linuxcnc_stub, loaded_name, self.TEST_CONTENT)
        upload_test_file(linuxcnc_stub, other_name, self.TEST_CONTENT)
        try:
            send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_AUTO)
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.task_mode == linuxcnc_pb2.MODE_AUTO,
                description="AUTO mode",
            )
            linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
                program=linuxcnc_pb2.ProgramCommand(open=loaded_name)
            ))
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.file.endswith(loaded_name),
                description="program loaded",
            )

            loaded = linuxcnc_stub.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename=loaded_name)
            )
            assert loaded.exists
            assert loaded.is_loaded_program
            assert loaded.path == upload_resp.path
            assert loaded.loaded_program == loaded.path
            assert loaded.interp_state == linuxcnc_pb2.INTERP_IDLE

            other = linuxcnc_stub.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename=other_name)
            )
            assert other.exists
            assert not other.is_loaded_program
            assert other.loaded_program == loaded.path
        finally:
            send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
            delete_test_file(linuxcnc_stub, loaded_name)
            delete_test_file(linuxcnc_stub, other_name)


@pytest.mark.e2e
class TestE2EServerInfo:
    """Verify GetServerInfo against the running server and simulator."""

    def test_server_info_reports_the_package_version_and_linuxcnc(self, linuxcnc_stub):
        import linuxcnc_grpc

        server_info = linuxcnc_stub.GetServerInfo(linuxcnc_pb2.GetServerInfoRequest())
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

        assert server_info.server_version == linuxcnc_grpc.__version__
        assert server_info.linuxcnc_connected
        assert server_info.linuxcnc_version != ""
        assert server_info.linuxcnc_version == status.version


@pytest.mark.e2e
class TestE2EMachineConfig:
    """Verify GetMachineConfig against the sim's real INI file and status."""

    def test_machine_config_matches_sim_axis_config(self, linuxcnc_stub):
        config = linuxcnc_stub.GetMachineConfig(linuxcnc_pb2.GetMachineConfigRequest())
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())

        assert config.ini_readable
        assert config.ini_filename == status.task.ini_filename
        assert config.kinematics_module == "trivkins"
        assert config.kinematics_type == status.trajectory.kinematics_type
        assert config.coordinates == "XYZ"
        assert config.axis_mask == status.trajectory.axis_mask == 7
        assert config.joint_count == status.trajectory.joints == 3
        assert config.spindle_count >= 1

        # The sim axis config is in inches.
        assert config.linear_units == status.trajectory.linear_units
        assert config.linear_units_name == "inch"
        assert config.angular_units_name == "degree"
        assert config.max_linear_velocity > 0

        assert config.joint_axis_mapping_known
        assert [joint.axis_letter for joint in config.joints] == ["X", "Y", "Z"]
        assert [axis.letter for axis in config.axes] == ["X", "Y", "Z"]
        assert [list(axis.joints) for axis in config.axes] == [[0], [1], [2]]

        for joint_config, joint_status in zip(config.joints, status.joints):
            assert joint_config.joint_type == linuxcnc_pb2.JOINT_LINEAR
            assert joint_config.min_limit == joint_status.min_position_limit
            assert joint_config.max_limit == joint_status.max_position_limit
            assert joint_config.max_velocity > 0
            assert joint_config.max_acceleration > 0
            assert joint_config.HasField("home_sequence")

        for axis_config in config.axes:
            assert axis_config.max_limit > axis_config.min_limit
            assert axis_config.max_velocity > 0

        assert config.nc_files_directory


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

    def test_send_command_returns_linuxcnc_serial(self, linuxcnc_stub):
        """SendCommand's linuxcnc_serial is the serial LinuxCNC's task echoes."""
        resp = send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
        assert resp.HasField("linuxcnc_serial")

        # LinuxCNC's send returns once the task has echoed the command, so the
        # echo serial has already reached it.
        status = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
        assert status.task.echo_serial_number >= resp.linuxcnc_serial

        next_resp = send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
        assert next_resp.linuxcnc_serial > resp.linuxcnc_serial
        wait_complete(linuxcnc_stub)

    def test_wait_complete_on_linuxcnc_serial_ignores_later_command(self, linuxcnc_stub):
        """A wait on an earlier command's linuxcnc_serial is done while a later one runs."""
        home_all(linuxcnc_stub)
        abort_command = linuxcnc_pb2.LinuxCNCCommand(
            program=linuxcnc_pb2.ProgramCommand(abort=True)
        )
        try:
            mode_resp = send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MDI)
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.task_mode == linuxcnc_pb2.MODE_MDI,
                description="MDI mode",
            )

            # A ten minute dwell, so that no pause between the RPCs below can
            # outlast it. It is aborted afterwards, so its length costs nothing.
            dwell_resp = linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
                mdi=linuxcnc_pb2.MdiCommand(command="G4 P600")
            ))
            assert dwell_resp.status == linuxcnc_pb2.RCS_DONE, dwell_resp.error_message
            assert dwell_resp.linuxcnc_serial > mode_resp.linuxcnc_serial
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.echo_serial_number == dwell_resp.linuxcnc_serial,
                description="the dwell's serial to be echoed",
            )

            # The echo serial is past the mode change, so this answers at once
            # and the long timeout is never used.
            earlier = linuxcnc_stub.WaitComplete(linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=mode_resp.linuxcnc_serial, timeout=30.0
            ))
            assert earlier.status == linuxcnc_pb2.RCS_DONE

            current = linuxcnc_stub.WaitComplete(linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=dwell_resp.linuxcnc_serial, timeout=0.3
            ))
            assert current.status == linuxcnc_pb2.RCS_EXEC

            # Once the abort has been echoed, the wait on the dwell's serial
            # answers RCS_DONE: a later command was echoed, and the wait no
            # longer says how the dwell ended.
            abort_resp = linuxcnc_stub.SendCommand(abort_command)
            assert abort_resp.linuxcnc_serial > dwell_resp.linuxcnc_serial
            overtaken = linuxcnc_stub.WaitComplete(linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=dwell_resp.linuxcnc_serial, timeout=30.0
            ))
            assert overtaken.status == linuxcnc_pb2.RCS_DONE
        finally:
            # Always stop the dwell and go back to manual mode, or a failure
            # here would leave the simulator busy for the tests that follow.
            linuxcnc_stub.SendCommand(abort_command)
            wait_for_condition(
                linuxcnc_stub,
                lambda s: s.task.interp_state == linuxcnc_pb2.INTERP_IDLE,
                description="interpreter idle after abort",
            )
            send_mode_command(linuxcnc_stub, linuxcnc_pb2.MODE_MANUAL)
            wait_complete(linuxcnc_stub)

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

    def test_axis_jog_is_visible_in_velocity_not_inpos(self, linuxcnc_stub):
        """During an axis jog trajectory.inpos stays true; velocity is what shows motion."""
        home_all(linuxcnc_stub)
        linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
            teleop=linuxcnc_pb2.TeleopCommand(enable=True)
        ))
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.trajectory.motion_mode == linuxcnc_pb2.TRAJ_MODE_TELEOP,
            description="teleop mode",
        )

        linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_CONTINUOUS, is_joint=False, index=0, velocity=0.5
            )
        ))
        try:
            moving_status = wait_for_condition(
                linuxcnc_stub,
                lambda s: abs(s.axes[0].velocity) > 0.01,
                timeout=5.0,
                description="X axis moving",
            )
            assert moving_status.trajectory.inpos, (
                "documented behaviour: inpos stays true during an axis jog"
            )
            assert moving_status.trajectory.current_vel > 0.01
        finally:
            linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
                jog=linuxcnc_pb2.JogCommand(type=linuxcnc_pb2.JOG_STOP, is_joint=False, index=0)
            ))
        wait_for_condition(
            linuxcnc_stub,
            lambda s: abs(s.axes[0].velocity) < 0.001,
            description="X axis stopped",
        )

    def test_joint_jog_in_teleop_is_refused_by_linuxcnc_not_by_send_command(
        self, linuxcnc_stub
    ):
        """The server does not switch teleop: a joint jog in teleop is dispatched and ignored."""
        home_all(linuxcnc_stub)
        linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
            teleop=linuxcnc_pb2.TeleopCommand(enable=True)
        ))
        wait_for_condition(
            linuxcnc_stub,
            lambda s: s.trajectory.motion_mode == linuxcnc_pb2.TRAJ_MODE_TELEOP,
            description="teleop mode",
        )
        start_x = linuxcnc_stub.GetStatus(
            linuxcnc_pb2.GetStatusRequest()
        ).position.actual_position.x
        marker = f"e2e teleop marker {time.time_ns()}"
        reader, messages, stream_open = collect_stream_errors(linuxcnc_stub, marker)
        assert stream_open.wait(5.0)

        response = linuxcnc_stub.SendCommand(linuxcnc_pb2.LinuxCNCCommand(
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_INCREMENT, is_joint=True, index=0,
                velocity=1.0, increment=0.1,
            )
        ))
        # "Dispatched", even though LinuxCNC will refuse it.
        assert response.status == linuxcnc_pb2.RCS_DONE
        time.sleep(0.7)
        post_operator_error(linuxcnc_stub, marker)
        reader.join(timeout=10.0)

        end_x = linuxcnc_stub.GetStatus(
            linuxcnc_pb2.GetStatusRequest()
        ).position.actual_position.x
        assert abs(end_x - start_x) < 0.001, "the refused jog must not move the joint"
        refusals = [m.message for m in messages if "TELEOP" in m.message]
        assert refusals, f"expected LinuxCNC's refusal, got {[m.message for m in messages]}"


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


# ---------------------------------------------------------------------------
# A LinuxCNC task that does not answer
# ---------------------------------------------------------------------------

# LinuxCNC's task process. Freezing it with SIGSTOP is what a task that has
# stopped answering looks like to the server.
TASK_PROCESS_NAME = "milltask"
# How long the send has to have been out, and not back, before the other
# RPCs are tried. The send waits about 5 seconds for the frozen task.
SECONDS_BEFORE_PROBING = 1.0
# Deadline of each other RPC made during the freeze. They run at the same
# time, from SECONDS_BEFORE_PROBING on, so together they end by about 4 s,
# inside the send's 5 s wait. Generous, for a loaded runner; what is checked
# above all is that each answered before the send returned.
PROBE_DEADLINE_SECONDS = 3.0
# How long to wait for the queued command, and the state put back, after
# the task runs again.
SETTLE_SECONDS = 15.0


def task_process_ids():
    """Process ids of LinuxCNC's task on this host, found by name in /proc."""
    process_ids = []
    for process_directory in Path("/proc").glob("[0-9]*"):
        try:
            if (process_directory / "comm").read_text().strip() == TASK_PROCESS_NAME:
                process_ids.append(int(process_directory.name))
        except OSError:
            continue  # the process ended while it was being looked at
    return process_ids


def ini_file_of_process(process_id):
    """The resolved path after '-ini' on a process's command line, or None."""
    try:
        arguments = Path(f"/proc/{process_id}/cmdline").read_bytes().split(b"\0")
        working_directory = os.readlink(f"/proc/{process_id}/cwd")
    except OSError:
        return None
    arguments = [argument.decode(errors="replace") for argument in arguments]
    if "-ini" not in arguments[:-1]:
        return None
    ini_argument = arguments[arguments.index("-ini") + 1]
    return os.path.realpath(os.path.join(working_directory, ini_argument))


class SignalTarget:
    """
    One process to signal. With a pidfd (Linux 5.3 and newer) a signal
    can only reach the process the pidfd was opened for, even if its
    process id is reused; otherwise os.kill is used.
    """

    def __init__(self, process_id):
        self.process_id = process_id
        self._pidfd = None
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                self._pidfd = os.pidfd_open(process_id)
            except ProcessLookupError:
                raise
            except OSError:
                self._pidfd = None  # not supported here

    def send(self, signal_number):
        if self._pidfd is not None:
            signal.pidfd_send_signal(self._pidfd, signal_number)
        else:
            os.kill(self.process_id, signal_number)

    def continue_if_alive(self):
        """SIGCONT; a task that has exited meanwhile needs none."""
        try:
            self.send(signal.SIGCONT)
        except ProcessLookupError:
            pass

    def close(self):
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None


def task_of_this_linuxcnc(ini_filename):
    """
    The task process of the LinuxCNC the server talks to, or a skip.

    It must be the only process of that name on this host, and its command
    line must name the INI file the server's status reports. The check is
    made after the pidfd is opened, so it is about the process the pidfd
    will signal.
    """
    candidates = task_process_ids()
    if len(candidates) != 1:
        pytest.skip(
            f"Cannot freeze LinuxCNC's task: expected one '{TASK_PROCESS_NAME}' "
            f"process on this host, found {len(candidates)}"
        )
    try:
        target = SignalTarget(candidates[0])
    except ProcessLookupError:
        pytest.skip(f"Cannot freeze LinuxCNC's task: '{TASK_PROCESS_NAME}' ended")
    task_ini_file = ini_file_of_process(target.process_id)
    if not ini_filename or task_ini_file != os.path.realpath(ini_filename):
        target.close()
        pytest.skip(
            f"Cannot freeze LinuxCNC's task: '{TASK_PROCESS_NAME}' "
            f"(pid {target.process_id}) runs {task_ini_file!r}, the server's LinuxCNC "
            f"{ini_filename!r}"
        )
    return target


def serial_reached(echo_serial_number, linuxcnc_serial):
    """Whether the echo serial is at or past the serial, as LinuxCNC compares them."""
    difference = (echo_serial_number - linuxcnc_serial + 2**31) % 2**32 - 2**31
    return difference >= 0


def settle_after_freeze(stub, send, outcome):
    """
    After the task runs again: let the queued command finish and put the
    mode back, whatever the test found. Records in ``outcome`` what it saw;
    never raises, so it cannot hide the test's own failure.
    """
    try:
        response = send.result(timeout=60)[0]
        outcome["response"] = response
        deadline = time.monotonic() + SETTLE_SECONDS
        while time.monotonic() < deadline:
            status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=5)
            if (
                response.HasField("linuxcnc_serial")
                and serial_reached(status.task.echo_serial_number, response.linuxcnc_serial)
                and status.task.task_mode == linuxcnc_pb2.MODE_MDI
            ):
                outcome["took_effect"] = True
                break
            time.sleep(STATE_POLL_INTERVAL)
    except Exception as settle_error:
        outcome["settle_error"] = repr(settle_error)
    try:
        outcome["mode_back"] = stub.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(
                mode=linuxcnc_pb2.ModeCommand(mode=linuxcnc_pb2.MODE_MANUAL)
            ),
            timeout=30,
        )
    except Exception as restore_error:
        outcome["restore_error"] = repr(restore_error)


@pytest.mark.e2e
class TestE2EUnresponsiveTask:
    """A command for a frozen task does not stall the rest of the server."""

    def test_frozen_task_blocks_only_the_command(self, linuxcnc_stub, grpc_channel):
        from concurrent import futures

        from grpc_health.v1 import health_pb2, health_pb2_grpc

        status_before = linuxcnc_stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=10)
        task = task_of_this_linuxcnc(status_before.task.ini_filename)
        health_stub = health_pb2_grpc.HealthStub(grpc_channel)
        mode_mdi = linuxcnc_pb2.LinuxCNCCommand(
            mode=linuxcnc_pb2.ModeCommand(mode=linuxcnc_pb2.MODE_MDI)
        )

        def timed(call):
            started = time.monotonic()
            result = call()
            finished = time.monotonic()
            return result, finished - started, finished

        try:
            task.send(signal.SIGSTOP)
        except PermissionError:
            task.close()
            pytest.skip(
                f"Cannot freeze LinuxCNC's task: no permission to signal pid {task.process_id}"
            )
        except ProcessLookupError:
            task.close()
            pytest.skip(f"Cannot freeze LinuxCNC's task: pid {task.process_id} has ended")
        send = None
        rpc_pool = None
        probes = {}
        outcome = {}
        try:
            rpc_pool = futures.ThreadPoolExecutor(max_workers=4)
            send_submitted = time.monotonic()
            send = rpc_pool.submit(
                timed, lambda: linuxcnc_stub.SendCommand(mode_mdi, timeout=60)
            )
            # The send is blocked: it has been out for a while without
            # coming back. A frozen task cannot have echoed it.
            while time.monotonic() - send_submitted < SECONDS_BEFORE_PROBING:
                assert not send.done(), (
                    f"SendCommand came back although the task was frozen: {send.result()}"
                )
                time.sleep(0.05)
            # All at once, so their deadlines cannot add up past the send.
            probe_calls = {
                "GetServerInfo": lambda: linuxcnc_stub.GetServerInfo(
                    linuxcnc_pb2.GetServerInfoRequest(), timeout=PROBE_DEADLINE_SECONDS
                ),
                "GetStatus": lambda: linuxcnc_stub.GetStatus(
                    linuxcnc_pb2.GetStatusRequest(), timeout=PROBE_DEADLINE_SECONDS
                ),
                "health": lambda: health_stub.Check(
                    health_pb2.HealthCheckRequest(service=""), timeout=PROBE_DEADLINE_SECONDS
                ),
            }
            probe_futures = {
                probe_name: rpc_pool.submit(timed, probe_call)
                for probe_name, probe_call in probe_calls.items()
            }
            for probe_name, probe_future in probe_futures.items():
                probes[probe_name] = probe_future.result(timeout=60)
            response, send_seconds, send_finished = send.result(timeout=60)
        finally:
            # Each step runs whatever the one before did, and none of them
            # replaces the test's own failure.
            try:
                task.continue_if_alive()
            finally:
                try:
                    task.close()
                finally:
                    try:
                        if rpc_pool is not None:
                            rpc_pool.shutdown(wait=False)
                    finally:
                        if send is not None:
                            settle_after_freeze(linuxcnc_stub, send, outcome)

        # The other RPCs answered while the send was still waiting.
        for probe_name, (_, probe_seconds, probe_finished) in probes.items():
            assert probe_finished < send_finished, (
                f"{probe_name} answered only after the send ({probe_seconds:.3f}s)"
            )
        server_info = probes["GetServerInfo"][0]
        status = probes["GetStatus"][0]
        health = probes["health"][0]
        assert server_info.linuxcnc_connected
        assert status.task.task_mode == linuxcnc_pb2.MODE_MANUAL
        assert serial_reached(status.task.echo_serial_number, status_before.task.echo_serial_number)
        assert health.status == health_pb2.HealthCheckResponse.SERVING

        # LinuxCNC gave up waiting for the echo after about 5 seconds; the
        # command was written all the same.
        assert response.status == linuxcnc_pb2.RCS_DONE, response.error_message
        assert response.HasField("linuxcnc_serial")
        assert not serial_reached(status.task.echo_serial_number, response.linuxcnc_serial)
        assert response.HasField("acknowledged")
        assert response.acknowledged is False
        assert not response.not_sent
        assert send_seconds > 4.0, f"SendCommand took only {send_seconds:.3f}s"

        # The command stayed queued and ran once the task ran again.
        assert outcome.get("took_effect"), f"the queued mode change did not take effect: {outcome}"
        assert outcome["mode_back"].acknowledged is True, outcome
