"""
Unit tests for the ReadFile and ResolveFile RPCs, and for races and symlink
loops in the file RPCs generally.
"""

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
import pytest


@pytest.fixture
def nc_files_dir(tmp_path):
    """A real, canonical NC files directory for one test."""
    directory = Path(os.path.realpath(tmp_path)) / "nc_files"
    directory.mkdir()
    return directory


@pytest.fixture
def file_service(mock_linuxcnc_module, mock_linuxcnc_stat, nc_files_dir):
    """A servicer whose NC files directory is the temporary one."""
    mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
    mock_linuxcnc_module.command.return_value = MagicMock()
    mock_linuxcnc_module.error_channel.return_value = MagicMock()

    # Import the protobuf modules before patch.dict: anything first imported
    # inside it is dropped from sys.modules on exit.
    from linuxcnc_pb import linuxcnc_pb2  # noqa: F401

    with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
        sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
        from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

        yield LinuxCNCServiceServicer(nc_files_dir=str(nc_files_dir))


def _aborted_with(mock_grpc_context):
    """The status code of the single abort the RPC made."""
    mock_grpc_context.abort.assert_called_once()
    return mock_grpc_context.abort.call_args[0][0]


class TestReadFile:
    """Test ReadFile RPC."""

    def test_reads_whole_small_file(self, file_service, nc_files_dir, mock_grpc_context):
        """A file under the chunk limit comes back in one response."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"G0 X1\nM2\n")

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert response.content == b"G0 X1\nM2\n"
        assert response.path == str(program)
        assert response.offset == 0
        assert response.total_size_bytes == 9
        assert response.end_of_file is True
        assert response.modified_timestamp == program.stat().st_mtime_ns
        mock_grpc_context.abort.assert_not_called()

    def test_reads_in_chunks_with_offset(self, file_service, nc_files_dir, mock_grpc_context):
        """offset and max_bytes page through the file; only the last chunk is end_of_file."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"0123456789")

        first_chunk = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc", max_bytes=4), mock_grpc_context
        )
        second_chunk = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc", offset=4, max_bytes=4),
            mock_grpc_context,
        )
        last_chunk = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc", offset=8, max_bytes=4),
            mock_grpc_context,
        )

        assert (first_chunk.content, first_chunk.end_of_file) == (b"0123", False)
        assert (second_chunk.content, second_chunk.end_of_file) == (b"4567", False)
        assert (last_chunk.content, last_chunk.end_of_file) == (b"89", True)
        assert second_chunk.offset == 4
        assert first_chunk.total_size_bytes == 10

    def test_chunk_is_capped_at_the_server_limit(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Neither max_bytes=0 nor an oversized max_bytes returns more than the cap."""
        from linuxcnc_grpc.linuxcnc_service import MAX_READ_CHUNK_BYTES
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "big.ngc").write_bytes(b"x" * (MAX_READ_CHUNK_BYTES + 10))

        default_chunk = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="big.ngc"), mock_grpc_context
        )
        oversized_chunk = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(
                filename="big.ngc", max_bytes=MAX_READ_CHUNK_BYTES * 3
            ),
            mock_grpc_context,
        )
        remainder = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="big.ngc", offset=MAX_READ_CHUNK_BYTES),
            mock_grpc_context,
        )

        assert len(default_chunk.content) == MAX_READ_CHUNK_BYTES
        assert default_chunk.end_of_file is False
        assert len(oversized_chunk.content) == MAX_READ_CHUNK_BYTES
        assert len(remainder.content) == 10
        assert remainder.end_of_file is True

    def test_offset_past_the_end_returns_empty_end_of_file(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Reading past the end is not an error: empty content, end_of_file set."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"abc")

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc", offset=50), mock_grpc_context
        )

        assert response.content == b""
        assert response.end_of_file is True
        assert response.total_size_bytes == 3

    def test_empty_file(self, file_service, nc_files_dir, mock_grpc_context):
        """An empty file reads as empty content at end_of_file."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "empty.ngc").write_bytes(b"")

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="empty.ngc"), mock_grpc_context
        )

        assert response.content == b""
        assert response.end_of_file is True

    def test_non_utf8_bytes_are_returned_unchanged(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Content is bytes, so a file that is not valid UTF-8 still reads."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "latin1.ngc").write_bytes(b"(caf\xe9)\n")

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="latin1.ngc"), mock_grpc_context
        )

        assert response.content == b"(caf\xe9)\n"

    def test_reads_from_subdirectory(self, file_service, nc_files_dir, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "jobs").mkdir()
        (nc_files_dir / "jobs" / "a.ngc").write_bytes(b"M2\n")

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="jobs/a.ngc"), mock_grpc_context
        )

        assert response.content == b"M2\n"

    def test_missing_file_is_not_found(self, file_service, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="nope.ngc"), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.NOT_FOUND

    def test_directory_is_rejected(self, file_service, nc_files_dir, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "jobs").mkdir()

        file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="jobs"), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    @pytest.mark.parametrize(
        "filename", ["../outside.ngc", "/etc/passwd", "", "a\x00b.ngc", "jobs/../../x"]
    )
    def test_rejects_names_outside_the_directory(
        self, file_service, nc_files_dir, mock_grpc_context, filename
    ):
        """The same path rules as UploadFile and DeleteFile."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir.parent / "outside.ngc").write_bytes(b"secret")

        file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename=filename), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    def test_symlink_pointing_outside_is_rejected(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """A symlink inside the directory cannot be used to read a file outside it."""
        from linuxcnc_pb import linuxcnc_pb2

        outside_file = nc_files_dir.parent / "outside.ngc"
        outside_file.write_bytes(b"secret")
        (nc_files_dir / "link.ngc").symlink_to(outside_file)

        file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="link.ngc"), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    def test_symlink_inside_the_directory_is_followed(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """A symlink to another file in the directory reads that file and reports its path."""
        from linuxcnc_pb import linuxcnc_pb2

        target = nc_files_dir / "real.ngc"
        target.write_bytes(b"M2\n")
        (nc_files_dir / "link.ngc").symlink_to(target)

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="link.ngc"), mock_grpc_context
        )

        assert response.content == b"M2\n"
        assert response.path == str(target)

    def test_file_swapped_for_a_symlink_after_validation_is_not_followed(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """If the validated file becomes a symlink before the open, the read fails."""
        from linuxcnc_pb import linuxcnc_pb2

        outside_file = nc_files_dir.parent / "outside.ngc"
        outside_file.write_bytes(b"secret")
        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        validate_nc_path = file_service._validate_nc_path

        def validate_then_swap(filename):
            resolved = validate_nc_path(filename)
            program.unlink()
            program.symlink_to(outside_file)
            return resolved

        with patch.object(file_service, "_validate_nc_path", side_effect=validate_then_swap):
            response = file_service.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL

    def test_file_truncated_before_the_read_reports_end_of_file(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Size is sampled after the read, so a shrunken file ends the download cleanly."""
        import os as os_module
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"0123456789")
        real_fdopen = os_module.fdopen

        def fdopen_after_truncation(file_descriptor, mode):
            program.write_bytes(b"01")  # another client replaces the file
            return real_fdopen(file_descriptor, mode)

        with patch("linuxcnc_grpc.linuxcnc_service.os.fdopen", side_effect=fdopen_after_truncation):
            response = file_service.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="part.ngc", offset=4, max_bytes=4),
                mock_grpc_context,
            )

        assert response.content == b""
        assert response.total_size_bytes == 2
        assert response.end_of_file is True

    @pytest.mark.parametrize(
        "rewritten_content",
        [b"9876543210", b"0123456789 and more"],
        ids=["same_size", "grown"],
    )
    def test_file_rewritten_during_the_read_is_aborted(
        self, file_service, nc_files_dir, mock_grpc_context, rewritten_content
    ):
        """Bytes of one version are never returned with the metadata of another."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"0123456789")
        os.utime(program, ns=(1_000_000_000, 1_000_000_000))
        real_fstat = os.fstat
        fstat_calls = []

        def fstat_then_rewrite(file_descriptor):
            stat_result = real_fstat(file_descriptor)
            fstat_calls.append(file_descriptor)
            if len(fstat_calls) == 1:
                # Another client rewrites the file in place after the first
                # sample; a fixed, different mtime keeps this independent of
                # the clock's resolution.
                program.write_bytes(rewritten_content)
                os.utime(program, ns=(2_000_000_000, 2_000_000_000))
            return stat_result

        with patch("linuxcnc_grpc.linuxcnc_service.os.fstat", side_effect=fstat_then_rewrite):
            response = file_service.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.ABORTED
        assert "changed while it was being read" in mock_grpc_context.abort.call_args[0][1]

    def test_unchanged_file_reports_the_metadata_of_the_bytes_read(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Paging through an unchanged file is not disturbed by the change check."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"0123456789")

        response = file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc", offset=2, max_bytes=3),
            mock_grpc_context,
        )

        assert response.content == b"234"
        assert response.total_size_bytes == 10
        assert response.end_of_file is False
        assert response.modified_timestamp == program.stat().st_mtime_ns
        mock_grpc_context.abort.assert_not_called()

    def test_file_swapped_for_a_fifo_before_the_open_does_not_block(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """A FIFO put in place after the regular-file check is rejected, not waited on."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        real_open = os.open
        swapped = []
        responses = []

        def swap_for_fifo_then_open(path, flags, *args, **kwargs):
            if not swapped and Path(path) == program:
                swapped.append(path)
                program.unlink()
                os.mkfifo(program)
            return real_open(path, flags, *args, **kwargs)

        def read_file():
            responses.append(file_service.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
            ))

        with patch("linuxcnc_grpc.linuxcnc_service.os.open", side_effect=swap_for_fifo_then_open):
            reader = threading.Thread(target=read_file, daemon=True)
            reader.start()
            reader.join(timeout=10)
            read_returned_without_a_writer = not reader.is_alive()
        if not read_returned_without_a_writer:
            # Let a reader stuck in open() go, so the thread does not linger.
            os.close(real_open(program, os.O_RDWR | os.O_NONBLOCK))
            reader.join(timeout=5)

        assert swapped
        assert read_returned_without_a_writer, "ReadFile blocked opening a FIFO"
        assert responses == [None]
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    @pytest.mark.parametrize("field", ["offset", "max_bytes"])
    def test_negative_offset_or_size_is_rejected(
        self, file_service, nc_files_dir, mock_grpc_context, field
    ):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"abc")

        file_service.ReadFile(
            linuxcnc_pb2.ReadFileRequest(filename="part.ngc", **{field: -1}),
            mock_grpc_context,
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    def test_does_not_need_the_linuxcnc_lock(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Like the other file RPCs, a read works while the lock is held elsewhere."""
        import threading
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"abc")
        responses = []

        def read_file():
            responses.append(file_service.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
            ))

        with file_service._lock:
            reader = threading.Thread(target=read_file)
            reader.start()
            reader.join(timeout=5)

        assert not reader.is_alive()
        assert responses[0].content == b"abc"


class TestResolveFile:
    """Test ResolveFile RPC."""

    def test_plain_file_that_is_not_loaded(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"M2\n")
        mock_linuxcnc_stat.file = ""

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert response.path == str(nc_files_dir / "part.ngc")
        assert response.exists is True
        assert response.is_directory is False
        assert response.is_symlink is False
        assert response.is_loaded_program is False
        assert response.loaded_program == ""
        assert response.interp_state == linuxcnc_pb2.INTERP_IDLE

    def test_loaded_program_is_recognised(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        mock_linuxcnc_stat.file = str(program)

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert response.is_loaded_program is True
        assert response.loaded_program == str(program)

    def test_other_file_while_a_program_is_loaded(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        (nc_files_dir / "other.ngc").write_bytes(b"M2\n")
        mock_linuxcnc_stat.file = str(program)

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="other.ngc"), mock_grpc_context
        )

        assert response.is_loaded_program is False
        assert response.loaded_program == str(program)

    def test_symlink_to_the_loaded_program(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A different name that resolves to the loaded program is the loaded program."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        (nc_files_dir / "alias.ngc").symlink_to(program)
        mock_linuxcnc_stat.file = str(program)

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="alias.ngc"), mock_grpc_context
        )

        assert response.path == str(program)
        assert response.is_symlink is True
        assert response.is_loaded_program is True

    def test_symlinked_directory_then_dotdot_counts_as_symlink(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """link/../part.ngc follows a symlink even though it ends at the plain path."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "jobs").mkdir()
        (nc_files_dir / "alias").symlink_to(nc_files_dir / "jobs")
        (nc_files_dir / "part.ngc").write_bytes(b"M2\n")

        through_link = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="alias/../part.ngc"), mock_grpc_context
        )
        plain_dotdot = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="jobs/../part.ngc"), mock_grpc_context
        )

        assert through_link.path == str(nc_files_dir / "part.ngc")
        assert through_link.is_symlink is True
        assert plain_dotdot.is_symlink is False

    def test_file_inside_a_symlinked_directory_counts_as_symlink(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "jobs").mkdir()
        (nc_files_dir / "jobs" / "a.ngc").write_bytes(b"M2\n")
        (nc_files_dir / "alias").symlink_to(nc_files_dir / "jobs")

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="alias/a.ngc"), mock_grpc_context
        )

        assert response.path == str(nc_files_dir / "jobs" / "a.ngc")
        assert response.is_symlink is True

    def test_loaded_program_reported_through_a_symlink(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        """LinuxCNC may report the program by a symlinked or dotted path."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        (nc_files_dir / "alias.ngc").symlink_to(program)
        mock_linuxcnc_stat.file = str(nc_files_dir / "." / "alias.ngc")

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert response.is_loaded_program is True
        assert response.loaded_program == str(program)

    def test_hard_link_to_the_loaded_program(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A hard link has its own path but is the same file on disk."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"M2\n")
        os.link(program, nc_files_dir / "hardlink.ngc")
        mock_linuxcnc_stat.file = str(program)

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="hardlink.ngc"), mock_grpc_context
        )

        assert response.path == str(nc_files_dir / "hardlink.ngc")
        assert response.is_symlink is False
        assert response.is_loaded_program is True

    def test_name_that_does_not_exist(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        """A free name resolves (so a client can check before uploading) with exists=False."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.file = str(nc_files_dir / "part.ngc")

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="new.ngc"), mock_grpc_context
        )

        assert response.path == str(nc_files_dir / "new.ngc")
        assert response.exists is False
        assert response.is_loaded_program is False
        mock_grpc_context.abort.assert_not_called()

    def test_loaded_program_that_was_deleted_still_matches_by_path(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        """LinuxCNC keeps reporting a program whose file is gone; the name still matches."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.file = str(nc_files_dir / "gone.ngc")

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="gone.ngc"), mock_grpc_context
        )

        assert response.exists is False
        assert response.is_loaded_program is True

    def test_directory(self, file_service, nc_files_dir, mock_grpc_context):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "jobs").mkdir()

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="jobs"), mock_grpc_context
        )

        assert response.exists is True
        assert response.is_directory is True

    def test_reports_interpreter_state(
        self, file_service, nc_files_dir, mock_linuxcnc_module, mock_linuxcnc_stat,
        mock_grpc_context,
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.interp_state = mock_linuxcnc_module.INTERP_PAUSED

        response = file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert response.interp_state == linuxcnc_pb2.INTERP_PAUSED

    @pytest.mark.parametrize("filename", ["../outside.ngc", "/etc/passwd", "", "a\x00b"])
    def test_rejects_names_outside_the_directory(
        self, file_service, mock_grpc_context, filename
    ):
        from linuxcnc_pb import linuxcnc_pb2

        file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename=filename), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    def test_symlink_pointing_outside_is_rejected(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        outside_file = nc_files_dir.parent / "outside.ngc"
        outside_file.write_bytes(b"secret")
        (nc_files_dir / "link.ngc").symlink_to(outside_file)

        file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="link.ngc"), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT

    def test_linuxcnc_error_is_unavailable(
        self, file_service, mock_linuxcnc_module, mock_linuxcnc_stat, mock_grpc_context
    ):
        """Without LinuxCNC the loaded program is unknown, so the RPC fails rather than guess."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.poll.side_effect = mock_linuxcnc_module.error("gone")

        file_service.ResolveFile(
            linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.UNAVAILABLE


class TestUploadFileExclusiveCreate:
    """UploadFile with fail_if_exists when two uploads race for one new name."""

    def test_two_concurrent_uploads_of_a_new_name_only_one_writes(
        self, file_service, nc_files_dir
    ):
        """Both pass the existence check; the loser gets ALREADY_EXISTS and writes nothing."""
        from linuxcnc_pb import linuxcnc_pb2

        both_passed_the_existence_check = threading.Barrier(2, timeout=10)
        real_mkdir = Path.mkdir

        def mkdir_after_both_checked(directory, *args, **kwargs):
            # mkdir runs between the existence check and the write.
            both_passed_the_existence_check.wait()
            return real_mkdir(directory, *args, **kwargs)

        upload_contents = ["G0 X1\n", "G0 X2\n"]
        upload_contexts = []
        upload_responses = [None, None]
        for _ in upload_contents:
            upload_context = MagicMock(spec=grpc.ServicerContext)
            upload_context.is_active.return_value = True
            upload_contexts.append(upload_context)

        def upload(upload_index):
            upload_responses[upload_index] = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="part.ngc",
                    content=upload_contents[upload_index],
                    fail_if_exists=True,
                ),
                upload_contexts[upload_index],
            )

        with patch.object(Path, "mkdir", mkdir_after_both_checked):
            upload_threads = [
                threading.Thread(target=upload, args=(upload_index,), daemon=True)
                for upload_index in range(len(upload_contents))
            ]
            for upload_thread in upload_threads:
                upload_thread.start()
            for upload_thread in upload_threads:
                upload_thread.join(timeout=20)
        assert not any(upload_thread.is_alive() for upload_thread in upload_threads)

        winning_indexes = [
            upload_index
            for upload_index, upload_response in enumerate(upload_responses)
            if upload_response is not None
        ]
        assert len(winning_indexes) == 1
        winning_index = winning_indexes[0]
        losing_index = 1 - winning_index
        assert upload_responses[winning_index].overwritten is False
        upload_contexts[winning_index].abort.assert_not_called()
        assert _aborted_with(upload_contexts[losing_index]) == grpc.StatusCode.ALREADY_EXISTS
        assert (nc_files_dir / "part.ngc").read_text() == upload_contents[winning_index]

    def test_existing_file_is_still_refused_and_left_alone(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_text("old")

        file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(
                filename="part.ngc", content="new", fail_if_exists=True
            ),
            mock_grpc_context,
        )

        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.ALREADY_EXISTS
        assert (nc_files_dir / "part.ngc").read_text() == "old"

    def test_new_file_in_a_new_subdirectory_is_created(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(
                filename="jobs/part.ngc", content="M2\n", fail_if_exists=True
            ),
            mock_grpc_context,
        )

        assert response.overwritten is False
        assert (nc_files_dir / "jobs" / "part.ngc").read_text() == "M2\n"
        mock_grpc_context.abort.assert_not_called()

    def test_without_fail_if_exists_a_file_created_meanwhile_is_overwritten(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Plain uploads keep replacing whatever is there."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_text("old")

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(filename="part.ngc", content="new"),
            mock_grpc_context,
        )

        assert response.overwritten is True
        assert (nc_files_dir / "part.ngc").read_text() == "new"


class TestSymlinkLoop:
    """A symlink loop in a name is an invalid argument on every Python version."""

    @staticmethod
    def _file_rpc_calls():
        from linuxcnc_pb import linuxcnc_pb2

        return {
            "ReadFile": lambda name: linuxcnc_pb2.ReadFileRequest(filename=name),
            "UploadFile": lambda name: linuxcnc_pb2.UploadFileRequest(
                filename=name, content="M2\n"
            ),
            "DeleteFile": lambda name: linuxcnc_pb2.DeleteFileRequest(filename=name),
            "ResolveFile": lambda name: linuxcnc_pb2.ResolveFileRequest(filename=name),
            "ListFiles": lambda name: linuxcnc_pb2.ListFilesRequest(subdirectory=name),
        }

    @pytest.mark.parametrize(
        "rpc_name", ["ReadFile", "UploadFile", "DeleteFile", "ResolveFile", "ListFiles"]
    )
    @pytest.mark.parametrize("looping_name", ["loop.ngc", "loop.ngc/part.ngc"])
    def test_loop_is_invalid_argument(
        self, file_service, nc_files_dir, mock_grpc_context, rpc_name, looping_name
    ):
        (nc_files_dir / "loop.ngc").symlink_to(nc_files_dir / "other.ngc")
        (nc_files_dir / "other.ngc").symlink_to(nc_files_dir / "loop.ngc")

        response = getattr(file_service, rpc_name)(
            self._file_rpc_calls()[rpc_name](looping_name), mock_grpc_context
        )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INVALID_ARGUMENT
        assert (nc_files_dir / "loop.ngc").is_symlink()
        assert (nc_files_dir / "other.ngc").is_symlink()

    def test_loop_is_a_value_error_when_resolve_does_not_raise(
        self, file_service, nc_files_dir
    ):
        """Newer Pythons resolve a loop silently; the loop is still refused."""
        (nc_files_dir / "loop.ngc").symlink_to(nc_files_dir / "loop.ngc")

        def resolve_without_loop_detection(path, strict=False):
            return Path(os.path.abspath(path))

        with patch.object(Path, "resolve", resolve_without_loop_detection):
            with pytest.raises(ValueError, match="symbolic links"):
                file_service._validate_nc_path("loop.ngc")

    def test_loop_is_a_value_error_when_resolve_raises_runtime_error(
        self, file_service, nc_files_dir
    ):
        """Older Pythons report a loop from resolve() as RuntimeError."""
        real_resolve = Path.resolve

        def resolve_reporting_a_loop(path, strict=False):
            if path.name == "part.ngc":
                raise RuntimeError(f"Symlink loop from {path}")
            return real_resolve(path, strict=strict)

        with patch.object(Path, "resolve", resolve_reporting_a_loop):
            with pytest.raises(ValueError, match="symbolic links"):
                file_service._validate_nc_path("part.ngc")

    def test_long_chain_of_links_without_a_loop_still_resolves(
        self, file_service, nc_files_dir
    ):
        target = nc_files_dir / "part.ngc"
        target.write_bytes(b"M2\n")
        previous_name = target
        for link_number in range(5):
            link = nc_files_dir / f"link{link_number}.ngc"
            link.symlink_to(previous_name)
            previous_name = link

        assert file_service._validate_nc_path("link4.ngc") == target


class TestSymlinkLoopOutsideTheName:
    """
    Loops that _validate_nc_path does not see: in the NC files directory
    itself and in the path of the loaded program. Python <= 3.12 reports them
    from resolve() as RuntimeError, which must not escape the RPC.
    """

    @staticmethod
    def _resolve_reporting_a_loop_for(looping_path):
        real_resolve = Path.resolve

        def resolve_reporting_a_loop(path, strict=False):
            if str(path) == str(looping_path):
                raise RuntimeError(f"Symlink loop from {path}")
            return real_resolve(path, strict=strict)

        return resolve_reporting_a_loop

    def test_loaded_program_in_a_loop_is_not_the_loaded_program(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"M2\n")
        looping_program = "/elsewhere/loop.ngc"
        mock_linuxcnc_stat.file = looping_program

        with patch.object(
            Path, "resolve", self._resolve_reporting_a_loop_for(looping_program)
        ):
            response = file_service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
            )

        mock_grpc_context.abort.assert_not_called()
        assert response.path == str(nc_files_dir / "part.ngc")
        assert response.is_loaded_program is False
        assert response.loaded_program == looping_program

    @pytest.mark.parametrize(
        "rpc_name, make_request",
        [
            (
                "ResolveFile",
                lambda linuxcnc_pb2: linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"),
            ),
            ("ListFiles", lambda linuxcnc_pb2: linuxcnc_pb2.ListFilesRequest()),
        ],
    )
    def test_nc_files_directory_in_a_loop_is_internal(
        self, file_service, nc_files_dir, mock_grpc_context, rpc_name, make_request
    ):
        from linuxcnc_pb import linuxcnc_pb2

        with patch.object(
            Path, "resolve", self._resolve_reporting_a_loop_for(nc_files_dir)
        ):
            response = getattr(file_service, rpc_name)(
                make_request(linuxcnc_pb2), mock_grpc_context
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL

    @pytest.mark.parametrize(
        "rpc_name, make_request",
        [
            (
                "ResolveFile",
                lambda linuxcnc_pb2: linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"),
            ),
            ("ListFiles", lambda linuxcnc_pb2: linuxcnc_pb2.ListFilesRequest()),
        ],
    )
    def test_nc_files_directory_in_a_loop_is_internal_when_resolve_does_not_raise(
        self, mock_linuxcnc_module, mock_linuxcnc_stat, tmp_path, mock_grpc_context,
        rpc_name, make_request,
    ):
        """Newer Pythons resolve a loop silently; the kernel is asked instead."""
        from linuxcnc_pb import linuxcnc_pb2

        looping_directory = Path(os.path.realpath(tmp_path)) / "nc_files_loop"
        looping_directory.symlink_to(looping_directory)
        mock_linuxcnc_module.stat.return_value = mock_linuxcnc_stat
        mock_linuxcnc_module.command.return_value = MagicMock()
        mock_linuxcnc_module.error_channel.return_value = MagicMock()

        def resolve_without_loop_detection(path, strict=False):
            return Path(os.path.abspath(path))

        with patch.dict(sys.modules, {"linuxcnc": mock_linuxcnc_module}):
            sys.modules.pop("linuxcnc_grpc.linuxcnc_service", None)
            from linuxcnc_grpc.linuxcnc_service import LinuxCNCServiceServicer

            service = LinuxCNCServiceServicer(nc_files_dir=str(looping_directory))
            with patch.object(Path, "resolve", resolve_without_loop_detection):
                response = getattr(service, rpc_name)(
                    make_request(linuxcnc_pb2), mock_grpc_context
                )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert "symbolic links" in mock_grpc_context.abort.call_args[0][1]

    def test_directory_helper_reports_a_loop_as_eloop_when_resolve_does_not_raise(
        self, file_service, tmp_path
    ):
        import errno

        looping_directory = Path(os.path.realpath(tmp_path)) / "nc_files_loop"
        looping_directory.symlink_to(looping_directory)
        file_service._nc_files_dir = str(looping_directory)

        def resolve_without_loop_detection(path, strict=False):
            return Path(os.path.abspath(path))

        with patch.object(Path, "resolve", resolve_without_loop_detection):
            with pytest.raises(OSError) as raised:
                file_service._resolved_nc_files_dir()

        assert raised.value.errno == errno.ELOOP

    def test_directory_helper_still_resolves_a_directory_that_does_not_exist_yet(
        self, file_service, tmp_path
    ):
        """Only too many links is an error here; a missing directory is not."""
        missing_directory = Path(os.path.realpath(tmp_path)) / "not_created_yet"
        file_service._nc_files_dir = str(missing_directory)

        assert file_service._resolved_nc_files_dir() == missing_directory

    def test_directory_helper_follows_a_link_to_the_directory(
        self, file_service, nc_files_dir, tmp_path
    ):
        link_to_directory = Path(os.path.realpath(tmp_path)) / "link_to_nc_files"
        link_to_directory.symlink_to(nc_files_dir)
        file_service._nc_files_dir = str(link_to_directory)

        assert file_service._resolved_nc_files_dir() == nc_files_dir


class TestTooManyLinksMessage:
    """
    A chain of links longer than the kernel follows is refused like a loop
    (the kernel could not open it either). The message must not call it a loop.
    """

    def test_chain_longer_than_the_kernel_follows_is_refused_without_claiming_a_loop(
        self, file_service, nc_files_dir
    ):
        target = nc_files_dir / "part.ngc"
        target.write_bytes(b"M2\n")
        previous_name = target
        # Far more links than any kernel follows (Linux: 40, macOS: 32).
        for link_number in range(100):
            link = nc_files_dir / f"link{link_number}.ngc"
            link.symlink_to(previous_name)
            previous_name = link

        with pytest.raises(ValueError) as raised:
            file_service._validate_nc_path("link99.ngc")

        message = str(raised.value)
        assert "Too many levels of symbolic links" in message
        assert "loop" not in message.lower()
        assert "cycle" not in message.lower()
        assert "cyclic" not in message.lower()


class TestUploadFileFailedWrite:
    """UploadFile with fail_if_exists must not leave a partial file behind."""

    class _FileFailingOn:
        """Wraps a real open file; the named operation fails as a full disk would."""

        def __init__(self, real_file, failing_operation):
            self._real_file = real_file
            self._failing_operation = failing_operation

        def __enter__(self):
            return self

        def write(self, content):
            # Part of the content reaches the disk before the failure.
            self._real_file.write(content[: len(content) // 2])
            self._real_file.flush()
            if self._failing_operation == "write":
                raise OSError(28, "No space left on device")

        def __exit__(self, exception_type, exception, traceback):
            self._real_file.close()
            if self._failing_operation == "close" and exception_type is None:
                raise OSError(28, "No space left on device")
            return False

    @pytest.mark.parametrize("failing_operation", ["write", "close"])
    def test_failed_write_removes_the_file_so_a_retry_succeeds(
        self, file_service, nc_files_dir, mock_grpc_context, failing_operation
    ):
        from linuxcnc_pb import linuxcnc_pb2

        real_open = Path.open
        file_wrapper = self._FileFailingOn

        def open_then_fail(path, mode="r", *args, **kwargs):
            real_file = real_open(path, mode, *args, **kwargs)
            if mode == "x":
                return file_wrapper(real_file, failing_operation)
            return real_file

        request = linuxcnc_pb2.UploadFileRequest(
            filename="new.ngc", content="G0 X1\nG0 X2\nM2\n", fail_if_exists=True
        )
        with patch.object(Path, "open", open_then_fail):
            response = file_service.UploadFile(request, mock_grpc_context)

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert not (nc_files_dir / "new.ngc").exists()

        retry_context = MagicMock(spec=grpc.ServicerContext)
        retry_response = file_service.UploadFile(request, retry_context)

        retry_context.abort.assert_not_called()
        assert retry_response.overwritten is False
        assert (nc_files_dir / "new.ngc").read_text() == "G0 X1\nG0 X2\nM2\n"

    def test_file_that_another_upload_created_is_not_removed(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Losing the exclusive create must leave the winner's file alone."""
        from linuxcnc_pb import linuxcnc_pb2

        real_mkdir = Path.mkdir

        def mkdir_then_another_upload_wins(directory, *args, **kwargs):
            # mkdir runs between the existence check and the exclusive create.
            real_mkdir(directory, *args, **kwargs)
            (nc_files_dir / "new.ngc").write_text("the other upload\n")

        with patch.object(Path, "mkdir", mkdir_then_another_upload_wins):
            response = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="new.ngc", content="M2\n", fail_if_exists=True
                ),
                mock_grpc_context,
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.ALREADY_EXISTS
        assert (nc_files_dir / "new.ngc").read_text() == "the other upload\n"
