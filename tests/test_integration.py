"""
Integration tests for LinuxCNC gRPC clients.

These tests start the mock server and connect real gRPC clients to verify
end-to-end functionality.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import grpc
import pytest

# Add src to path
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from linuxcnc_pb import (
    linuxcnc_pb2,
    linuxcnc_pb2_grpc,
    hal_pb2,
    hal_pb2_grpc,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MOCK_SERVER_PATH = Path(__file__).parent / "mock_server.py"

# The mock's large file: byte N is N modulo the period. Stated here rather
# than imported so the test checks the mock against an independent copy.
READ_CHUNK_CAP_BYTES = 1024 * 1024
LARGE_PROGRAM_PATTERN_PERIOD = 251
LARGE_PROGRAM_SIZE_BYTES = 2 * READ_CHUNK_CAP_BYTES + 4321


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file."""
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


class MockServerFixture:
    """Context manager for running mock server during tests."""

    def __init__(self, port: int = 50099):
        self.port = port
        self.process = None

    def __enter__(self):
        # Start mock server
        self.process = subprocess.Popen(
            [sys.executable, str(MOCK_SERVER_PATH), "--port", str(self.port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for ready signal
        deadline = time.time() + 10
        while time.time() < deadline:
            line = self.process.stdout.readline()
            if line.startswith("READY:"):
                break
            if self.process.poll() is not None:
                raise RuntimeError(f"Mock server failed to start: {self.process.stderr.read()}")
            time.sleep(0.1)
        else:
            self.process.kill()
            raise RuntimeError("Timeout waiting for mock server")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)

    @property
    def address(self) -> str:
        return f"localhost:{self.port}"


@pytest.fixture(scope="module")
def mock_server():
    """Start mock server for the test module."""
    with MockServerFixture(port=50099) as server:
        yield server


@pytest.fixture
def linuxcnc_client(mock_server):
    """Create LinuxCNC gRPC client connected to mock server."""
    channel = grpc.insecure_channel(mock_server.address)
    stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)
    yield stub
    channel.close()


@pytest.fixture
def hal_client(mock_server):
    """Create HAL gRPC client connected to mock server."""
    channel = grpc.insecure_channel(mock_server.address)
    stub = hal_pb2_grpc.HalServiceStub(channel)
    yield stub
    channel.close()


# =============================================================================
# LinuxCNC Service Tests
# =============================================================================

class TestLinuxCNCServiceIntegration:
    """Integration tests for LinuxCNC gRPC service."""

    def test_get_status(self, linuxcnc_client):
        """Test GetStatus returns expected mock data."""
        request = linuxcnc_pb2.GetStatusRequest()
        response = linuxcnc_client.GetStatus(request)

        # Load expected values from fixture
        expected = load_fixture("linuxcnc_status.json")

        # Verify task status
        assert response.task.task_mode == linuxcnc_pb2.MODE_MANUAL
        assert response.task.task_state == linuxcnc_pb2.STATE_ON
        assert response.task.exec_state == linuxcnc_pb2.EXEC_DONE
        assert response.task.interp_state == linuxcnc_pb2.INTERP_IDLE
        assert response.task.echo_serial_number == expected["task"]["echo_serial_number"]

        # Verify trajectory
        assert response.trajectory.joints == expected["trajectory"]["joints"]
        assert response.trajectory.enabled == expected["trajectory"]["enabled"]
        assert response.trajectory.feedrate == expected["trajectory"]["feedrate"]

        # Verify position
        assert response.position.actual_position.x == expected["position"]["actual_position"]["x"]
        assert response.position.actual_position.y == expected["position"]["actual_position"]["y"]
        assert response.position.actual_position.z == expected["position"]["actual_position"]["z"]

        # Verify joints
        assert len(response.joints) == 3
        for i, joint in enumerate(response.joints):
            assert joint.joint_number == i
            assert joint.homed == expected["joints"][i]["homed"]

        # Verify tool
        assert response.tool.tool_in_spindle == expected["tool"]["tool_in_spindle"]

    def test_send_command_state(self, linuxcnc_client):
        """Test SendCommand with state command."""
        command = linuxcnc_pb2.LinuxCNCCommand(
            serial=100,
            state=linuxcnc_pb2.StateCommand(state=linuxcnc_pb2.STATE_ON),
        )
        response = linuxcnc_client.SendCommand(command)

        assert response.serial == 100
        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.error_message == ""

    def test_send_command_mdi(self, linuxcnc_client):
        """Test SendCommand with MDI command."""
        command = linuxcnc_pb2.LinuxCNCCommand(
            serial=101,
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X10 Y10"),
        )
        response = linuxcnc_client.SendCommand(command)

        assert response.serial == 101
        assert response.status == linuxcnc_pb2.RCS_DONE

    def test_send_command_jog(self, linuxcnc_client):
        """Test SendCommand with jog command."""
        command = linuxcnc_pb2.LinuxCNCCommand(
            serial=102,
            jog=linuxcnc_pb2.JogCommand(
                type=linuxcnc_pb2.JOG_CONTINUOUS,
                is_joint=False,
                index=0,
                velocity=10.0,
            ),
        )
        response = linuxcnc_client.SendCommand(command)

        assert response.serial == 102
        assert response.status == linuxcnc_pb2.RCS_DONE

    def test_send_command_returns_linuxcnc_serial(self, linuxcnc_client):
        """SendCommand carries LinuxCNC's serial separately from the server's."""
        command = linuxcnc_pb2.LinuxCNCCommand(
            serial=103,
            mdi=linuxcnc_pb2.MdiCommand(command="G0 X1"),
        )
        response = linuxcnc_client.SendCommand(command)

        assert response.serial == 103
        assert response.HasField("linuxcnc_serial")
        assert response.linuxcnc_serial != response.serial

        wait_response = linuxcnc_client.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(
                linuxcnc_serial=response.linuxcnc_serial, timeout=5.0
            )
        )
        assert wait_response.status == linuxcnc_pb2.RCS_DONE

    def test_read_file(self, linuxcnc_client):
        """ReadFile returns the mock program, and pages through it by offset."""
        whole = linuxcnc_client.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="mock_program.ngc")
        )
        assert whole.content == b"G21\nG0 X1 Y1\nM2\n"
        assert whole.end_of_file
        assert whole.total_size_bytes == len(whole.content)

        first_chunk = linuxcnc_client.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="mock_program.ngc", max_bytes=4)
        )
        assert first_chunk.content == b"G21\n"
        assert not first_chunk.end_of_file

    def test_read_file_rejects_negative_offset_and_length(self, linuxcnc_client):
        """ReadFile refuses a negative offset or max_bytes, like the real server."""
        for invalid_request in (
            linuxcnc_pb2.ReadFileRequest(filename="mock_program.ngc", offset=-1),
            linuxcnc_pb2.ReadFileRequest(filename="mock_program.ngc", max_bytes=-1),
        ):
            with pytest.raises(grpc.RpcError) as error_info:
                linuxcnc_client.ReadFile(invalid_request)
            assert error_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_read_file_pages_through_a_large_file(self, linuxcnc_client):
        """A file larger than one response is read in capped chunks by offset."""
        expected_content = bytes(
            byte_index % LARGE_PROGRAM_PATTERN_PERIOD
            for byte_index in range(LARGE_PROGRAM_SIZE_BYTES)
        )
        received_content = bytearray()
        chunk_sizes = []
        modified_timestamps = set()
        while True:
            chunk = linuxcnc_client.ReadFile(
                linuxcnc_pb2.ReadFileRequest(
                    filename="mock_large_program.ngc",
                    offset=len(received_content),
                    # Above the cap on purpose: the server must still cap it.
                    max_bytes=LARGE_PROGRAM_SIZE_BYTES,
                )
            )
            assert chunk.offset == len(received_content)
            assert chunk.total_size_bytes == LARGE_PROGRAM_SIZE_BYTES
            assert len(chunk.content) <= READ_CHUNK_CAP_BYTES
            received_content.extend(chunk.content)
            chunk_sizes.append(len(chunk.content))
            modified_timestamps.add(chunk.modified_timestamp)
            if chunk.end_of_file:
                break
            assert chunk.content, "no data before the end of the file"

        assert chunk_sizes == [
            READ_CHUNK_CAP_BYTES,
            READ_CHUNK_CAP_BYTES,
            LARGE_PROGRAM_SIZE_BYTES - 2 * READ_CHUNK_CAP_BYTES,
        ]
        assert bytes(received_content) == expected_content
        assert len(modified_timestamps) == 1

    def test_read_file_past_the_end_is_empty(self, linuxcnc_client):
        """An offset at or past the end returns no content and end_of_file."""
        chunk = linuxcnc_client.ReadFile(
            linuxcnc_pb2.ReadFileRequest(
                filename="mock_large_program.ngc", offset=LARGE_PROGRAM_SIZE_BYTES + 10
            )
        )
        assert chunk.content == b""
        assert chunk.end_of_file

    def test_read_file_reports_a_change_between_pages(self, linuxcnc_client):
        """The changing file has a different modified_timestamp on later pages."""
        first_page = linuxcnc_client.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="mock_changing_program.ngc")
        )
        second_page = linuxcnc_client.ReadFile(
            linuxcnc_pb2.ReadFileRequest(
                filename="mock_changing_program.ngc", offset=len(first_page.content)
            )
        )
        assert not first_page.end_of_file
        assert second_page.modified_timestamp != first_page.modified_timestamp

    def test_read_file_not_found(self, linuxcnc_client):
        """ReadFile reports a missing file as NOT_FOUND."""
        with pytest.raises(grpc.RpcError) as error_info:
            linuxcnc_client.ReadFile(linuxcnc_pb2.ReadFileRequest(filename="missing.ngc"))
        assert error_info.value.code() == grpc.StatusCode.NOT_FOUND

    def test_resolve_file(self, linuxcnc_client):
        """ResolveFile says which name is the loaded program."""
        loaded = linuxcnc_client.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="mock_program.ngc")
        )
        assert loaded.is_loaded_program
        assert loaded.path == loaded.loaded_program

        other = linuxcnc_client.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="other.ngc")
        )
        assert not other.is_loaded_program
        assert not other.exists

    @pytest.mark.parametrize(
        "invalid_filename, expected_message",
        [
            ("", "Filename must not be empty"),
            ("   ", "Filename must not be empty"),
            ("part\x00.ngc", "Filename must not contain null bytes"),
            ("../outside.ngc", "Path must be within allowed directory: /mock/nc_files"),
            ("jobs/../../outside.ngc", "Path must be within allowed directory: /mock/nc_files"),
            ("/etc/passwd", "Path must be within allowed directory: /mock/nc_files"),
        ],
    )
    @pytest.mark.parametrize("rpc_name", ["ReadFile", "ResolveFile"])
    def test_file_rpcs_refuse_invalid_names(
        self, linuxcnc_client, rpc_name, invalid_filename, expected_message
    ):
        """ReadFile and ResolveFile refuse the names the real server refuses."""
        request_types = {
            "ReadFile": linuxcnc_pb2.ReadFileRequest,
            "ResolveFile": linuxcnc_pb2.ResolveFileRequest,
        }
        with pytest.raises(grpc.RpcError) as error_info:
            getattr(linuxcnc_client, rpc_name)(
                request_types[rpc_name](filename=invalid_filename)
            )
        assert error_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert error_info.value.details() == expected_message

    def test_file_rpcs_accept_a_name_with_dots_that_stays_inside(self, linuxcnc_client):
        """A ".." that does not leave the directory is not a traversal."""
        resolved = linuxcnc_client.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="jobs/../other.ngc")
        )
        assert not resolved.exists

    def test_wait_complete(self, linuxcnc_client):
        """Test WaitComplete returns immediately in mock."""
        request = linuxcnc_pb2.WaitCompleteRequest(serial=50, timeout=5.0)
        response = linuxcnc_client.WaitComplete(request)

        assert response.serial == 50
        assert response.status == linuxcnc_pb2.RCS_DONE
        assert response.error_message == ""

    def test_wait_complete_rejects_unissued_linuxcnc_serial(self, linuxcnc_client):
        """A linuxcnc_serial that SendCommand never returned gets RCS_ERROR."""
        issued_response = linuxcnc_client.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(mdi=linuxcnc_pb2.MdiCommand(command="G0 X2"))
        )
        unissued_serial = issued_response.linuxcnc_serial + 100000

        response = linuxcnc_client.WaitComplete(
            linuxcnc_pb2.WaitCompleteRequest(
                serial=51, linuxcnc_serial=unissued_serial, timeout=5.0
            )
        )

        assert response.serial == 51
        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert str(unissued_serial) in response.error_message

    def test_wait_complete_rejects_explicit_zero_linuxcnc_serial(self, linuxcnc_client):
        """An explicit linuxcnc_serial of 0 is a set field, not "no target"."""
        linuxcnc_client.SendCommand(
            linuxcnc_pb2.LinuxCNCCommand(mdi=linuxcnc_pb2.MdiCommand(command="G0 X3"))
        )
        request = linuxcnc_pb2.WaitCompleteRequest(serial=52, linuxcnc_serial=0, timeout=5.0)
        assert request.HasField("linuxcnc_serial")

        response = linuxcnc_client.WaitComplete(request)

        assert response.serial == 52
        assert response.status == linuxcnc_pb2.RCS_ERROR
        assert response.error_message

    def test_stream_status(self, linuxcnc_client):
        """Test StreamStatus returns multiple updates."""
        request = linuxcnc_pb2.StreamStatusRequest(interval_ms=50)
        stream = linuxcnc_client.StreamStatus(request)

        # Get a few updates
        updates = []
        for i, status in enumerate(stream):
            updates.append(status)
            if i >= 2:
                break

        assert len(updates) == 3
        for status in updates:
            assert status.task.task_mode == linuxcnc_pb2.MODE_MANUAL


# =============================================================================
# HAL Service Tests
# =============================================================================

class TestHalServiceIntegration:
    """Integration tests for HAL gRPC service."""

    def test_get_system_status(self, hal_client):
        """Test GetSystemStatus returns expected mock data."""
        request = hal_pb2.GetSystemStatusRequest()
        response = hal_client.GetSystemStatus(request)

        # Load expected values
        expected = load_fixture("hal_status.json")

        # Verify system info
        assert response.is_sim == expected["is_sim"]
        assert response.is_userspace == expected["is_userspace"]
        assert response.kernel_version == expected["kernel_version"]

        # Verify pins
        assert len(response.pins) == len(expected["pins"])
        pin_names = {p.name for p in response.pins}
        expected_pin_names = {p["name"] for p in expected["pins"]}
        assert pin_names == expected_pin_names

        # Verify signals
        assert len(response.signals) == len(expected["signals"])

        # Verify components
        assert len(response.components) == len(expected["components"])

    def test_query_pins_all(self, hal_client):
        """Test QueryPins with wildcard pattern."""
        request = hal_pb2.QueryPinsCommand(pattern="*")
        response = hal_client.QueryPins(request)

        assert response.success
        assert len(response.pins) == 3

    def test_query_pins_filtered(self, hal_client):
        """Test QueryPins with specific pattern."""
        request = hal_pb2.QueryPinsCommand(pattern="axis.*")
        response = hal_client.QueryPins(request)

        assert response.success
        assert len(response.pins) == 1
        assert response.pins[0].name == "axis.x.pos-cmd"

    def test_query_signals(self, hal_client):
        """Test QuerySignals."""
        request = hal_pb2.QuerySignalsCommand(pattern="*")
        response = hal_client.QuerySignals(request)

        assert response.success
        assert len(response.signals) == 2

    def test_query_params(self, hal_client):
        """Test QueryParams."""
        request = hal_pb2.QueryParamsCommand(pattern="*")
        response = hal_client.QueryParams(request)

        assert response.success
        assert len(response.params) == 3

    def test_query_components(self, hal_client):
        """Test QueryComponents."""
        request = hal_pb2.QueryComponentsCommand(pattern="*")
        response = hal_client.QueryComponents(request)

        assert response.success
        assert len(response.components) == 3
        component_names = {c.name for c in response.components}
        assert component_names == {"axis", "spindle", "iocontrol"}

    def test_get_value(self, hal_client):
        """Test GetValue returns a value."""
        request = hal_pb2.GetValueCommand(name="axis.x.pos-cmd")
        response = hal_client.GetValue(request)

        assert response.success
        assert response.type == hal_pb2.HAL_FLOAT
        assert response.value.float_value == 123.456

    def test_send_command(self, hal_client):
        """Test SendCommand returns success."""
        command = hal_pb2.HalCommand(
            serial=200,
            get_value=hal_pb2.GetValueCommand(name="axis.x.pos-cmd"),
        )
        response = hal_client.SendCommand(command)

        assert response.success
        assert response.serial == 200

    def test_stream_status(self, hal_client):
        """Test StreamStatus returns multiple updates."""
        request = hal_pb2.HalStreamStatusRequest(interval_ms=50)
        stream = hal_client.StreamStatus(request)

        updates = []
        for i, status in enumerate(stream):
            updates.append(status)
            if i >= 2:
                break

        assert len(updates) == 3
        for status in updates:
            assert status.is_sim is True


# =============================================================================
# Connection Tests
# =============================================================================

class TestConnection:
    """Test basic connection behavior."""

    def test_connection_refused_without_server(self):
        """Test that connection fails gracefully when server is not running."""
        channel = grpc.insecure_channel("localhost:59999")
        stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

        with pytest.raises(grpc.RpcError) as exc_info:
            stub.GetStatus(linuxcnc_pb2.GetStatusRequest(), timeout=1)

        assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
        channel.close()
