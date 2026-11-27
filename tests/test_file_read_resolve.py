"""
Unit tests for the ReadFile and ResolveFile RPCs, and for races and symlink
loops in the file RPCs generally.
"""

import errno
import io
import os
import stat as stat_module
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

        service = LinuxCNCServiceServicer(nc_files_dir=str(nc_files_dir))
        yield service
        # Stops the error reader thread, which would otherwise outlive the test.
        service.close()


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

    def test_paging_through_an_unchanged_file_reports_the_same_metadata(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Paging through an unchanged file is not disturbed by the change check."""
        from linuxcnc_pb import linuxcnc_pb2

        program = nc_files_dir / "part.ngc"
        program.write_bytes(b"0123456789")

        pages = []
        while not pages or not pages[-1].end_of_file:
            pages.append(
                file_service.ReadFile(
                    linuxcnc_pb2.ReadFileRequest(
                        filename="part.ngc",
                        offset=sum(len(page.content) for page in pages),
                        max_bytes=4,
                    ),
                    mock_grpc_context,
                )
            )

        assert [page.content for page in pages] == [b"0123", b"4567", b"89"]
        assert [page.offset for page in pages] == [0, 4, 8]
        assert [page.end_of_file for page in pages] == [False, False, True]
        assert {page.total_size_bytes for page in pages} == {10}
        assert {page.modified_timestamp for page in pages} == {program.stat().st_mtime_ns}
        mock_grpc_context.abort.assert_not_called()

    def test_descriptor_is_closed_when_wrapping_it_fails(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """If os.fdopen itself fails, the descriptor os.open returned is closed."""
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_bytes(b"M2\n")
        descriptors_handed_to_fdopen = []

        def fdopen_that_fails(file_descriptor, *args, **kwargs):
            descriptors_handed_to_fdopen.append(file_descriptor)
            raise OSError(errno.ENOMEM, "Cannot allocate memory")

        with patch("os.fdopen", fdopen_that_fails):
            response = file_service.ReadFile(
                linuxcnc_pb2.ReadFileRequest(filename="part.ngc"), mock_grpc_context
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert len(descriptors_handed_to_fdopen) == 1
        with pytest.raises(OSError) as closed_descriptor_error:
            os.fstat(descriptors_handed_to_fdopen[0])
        assert closed_descriptor_error.value.errno == errno.EBADF

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


class TestResolveFileWhileFilesChange:
    """ResolveFile's fields agree with each other when files change during the call."""

    @staticmethod
    def _stat_with_hook_after_the_poll(mock_linuxcnc_stat, watched_path, stats_before_hook, hook):
        """
        An os.stat stand-in that counts the stats of watched_path made after
        the status poll and runs hook once: right after stat number
        stats_before_hook (also when that stat raises), or before the first
        one if stats_before_hook is 0.
        """
        real_stat = os.stat
        state = {"polled": False, "watched_stats": 0}

        def mark_polled():
            state["polled"] = True

        mock_linuxcnc_stat.poll.side_effect = mark_polled

        def stat_with_hook(path, *args, **kwargs):
            if not state["polled"] or os.fspath(path) != os.fspath(watched_path):
                return real_stat(path, *args, **kwargs)
            if state["watched_stats"] == 0 and stats_before_hook == 0:
                hook()
            state["watched_stats"] += 1
            try:
                return real_stat(path, *args, **kwargs)
            finally:
                if state["watched_stats"] == stats_before_hook:
                    hook()

        return stat_with_hook

    def test_directory_created_during_the_call_is_not_a_missing_directory(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        """exists and is_directory come from one look at the path."""
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.file = ""
        appearing_directory = nc_files_dir / "jobs"

        stat_stand_in = self._stat_with_hook_after_the_poll(
            mock_linuxcnc_stat, appearing_directory, 1, appearing_directory.mkdir
        )
        with patch("os.stat", stat_stand_in):
            response = file_service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="jobs"), mock_grpc_context
            )

        mock_grpc_context.abort.assert_not_called()
        assert appearing_directory.is_dir()
        assert (response.exists, response.is_directory) == (False, False)

    def test_directory_removed_during_the_call_is_still_reported_consistently(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        mock_linuxcnc_stat.file = ""
        vanishing_directory = nc_files_dir / "jobs"
        vanishing_directory.mkdir()

        stat_stand_in = self._stat_with_hook_after_the_poll(
            mock_linuxcnc_stat, vanishing_directory, 1, vanishing_directory.rmdir
        )
        with patch("os.stat", stat_stand_in):
            response = file_service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="jobs"), mock_grpc_context
            )

        mock_grpc_context.abort.assert_not_called()
        assert not vanishing_directory.exists()
        assert (response.exists, response.is_directory) == (True, True)

    @pytest.mark.parametrize("looks_before_it_vanishes", [0, 1, 2])
    def test_loaded_program_that_vanishes_during_the_comparison_is_not_this_file(
        self, file_service, nc_files_dir, mock_linuxcnc_stat, mock_grpc_context,
        looks_before_it_vanishes,
    ):
        """
        Not an error: a file that is gone cannot be the one asked about.

        How many times the server looks at the loaded program differs
        between Python versions (resolve() looks once on some), so the
        program is removed after each number of looks in turn.
        """
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_text("M2\n")
        loaded_program = nc_files_dir / "loaded.ngc"
        loaded_program.write_text("M30\n")
        mock_linuxcnc_stat.file = str(loaded_program)

        stat_stand_in = self._stat_with_hook_after_the_poll(
            mock_linuxcnc_stat,
            loaded_program,
            looks_before_it_vanishes,
            lambda: loaded_program.unlink(missing_ok=True),
        )
        with patch("os.stat", stat_stand_in):
            response = file_service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
            )

        mock_grpc_context.abort.assert_not_called()
        assert response.exists is True
        assert response.is_loaded_program is False
        assert response.loaded_program == str(loaded_program)

    def test_loaded_program_in_a_loop_is_not_this_file_when_resolve_does_not_raise(
        self, file_service, nc_files_dir, tmp_path, mock_linuxcnc_stat, mock_grpc_context
    ):
        """
        Newer Pythons resolve a symlink loop without complaint, so the loop
        only shows when the loaded program is looked at. Not an error.
        """
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "part.ngc").write_text("M2\n")
        looping_program = Path(os.path.realpath(tmp_path)) / "loop.ngc"
        looping_program.symlink_to(looping_program)
        mock_linuxcnc_stat.file = str(looping_program)
        real_resolve = Path.resolve

        def resolve_without_loop_detection(path, strict=False):
            if str(path) == str(looping_program):
                return path
            return real_resolve(path, strict=strict)

        with patch.object(Path, "resolve", resolve_without_loop_detection):
            response = file_service.ResolveFile(
                linuxcnc_pb2.ResolveFileRequest(filename="part.ngc"), mock_grpc_context
            )

        mock_grpc_context.abort.assert_not_called()
        assert response.exists is True
        assert response.is_loaded_program is False
        assert response.loaded_program == str(looping_program)


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

    def test_two_uploads_fully_written_before_either_is_published_only_one_wins(
        self, file_service, nc_files_dir
    ):
        """Both have their content ready; the name goes to exactly one of them."""
        from linuxcnc_pb import linuxcnc_pb2

        both_ready_to_publish = threading.Barrier(2, timeout=10)
        real_link = os.link

        def link_after_both_are_written(source_path, destination_path, *args, **kwargs):
            both_ready_to_publish.wait()
            return real_link(source_path, destination_path, *args, **kwargs)

        upload_contents = ["G0 X1\n" * 100, "G0 X2\n" * 100]
        upload_contexts = [MagicMock(spec=grpc.ServicerContext) for _ in upload_contents]
        upload_responses = [None, None]

        def upload(upload_index):
            upload_responses[upload_index] = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="part.ngc",
                    content=upload_contents[upload_index],
                    fail_if_exists=True,
                ),
                upload_contexts[upload_index],
            )

        with patch("os.link", link_after_both_are_written):
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
        assert upload_responses[winning_index].overwritten is False
        upload_contexts[winning_index].abort.assert_not_called()
        assert (
            _aborted_with(upload_contexts[1 - winning_index])
            == grpc.StatusCode.ALREADY_EXISTS
        )
        assert (nc_files_dir / "part.ngc").read_text() == upload_contents[winning_index]
        assert [entry.name for entry in nc_files_dir.iterdir()] == ["part.ngc"]

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
            (
                "GetMachineConfig",
                lambda linuxcnc_pb2: linuxcnc_pb2.GetMachineConfigRequest(),
            ),
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
            try:
                with patch.object(Path, "resolve", resolve_without_loop_detection):
                    response = getattr(service, rpc_name)(
                        make_request(linuxcnc_pb2), mock_grpc_context
                    )
            finally:
                service.close()

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


class _FileFailingOn:
    """Wraps a real open file; the named operation fails as a full disk would."""

    def __init__(self, real_file, failing_operation, at_failure):
        self._real_file = real_file
        self._failing_operation = failing_operation
        self._at_failure = at_failure

    def __enter__(self):
        return self

    def __getattr__(self, attribute_name):
        return getattr(self._real_file, attribute_name)

    def write(self, content):
        # Part of the content reaches the disk before the failure.
        self._real_file.write(content[: len(content) // 2])
        self._real_file.flush()
        if self._failing_operation == "write":
            self._at_failure()
            raise OSError(errno.ENOSPC, "No space left on device")

    def __exit__(self, exception_type, exception, traceback):
        self._real_file.close()
        if self._failing_operation == "close" and exception_type is None:
            self._at_failure()
            raise OSError(errno.ENOSPC, "No space left on device")
        return False


def _first_write_fails_on(failing_operation, at_failure=lambda: None):
    """
    Patch io.open so the first file opened for writing fails in the named
    operation; at_failure runs just before it does.

    io.open is what Path.open, Path.write_text and os.fdopen all call, so
    the failure reaches an upload however the server opens its file.
    """
    real_open = io.open
    state = {"wrapped_one": False}

    def open_then_fail(file, mode="r", *args, **kwargs):
        real_file = real_open(file, mode, *args, **kwargs)
        if mode in ("w", "x") and not state["wrapped_one"]:
            state["wrapped_one"] = True
            return _FileFailingOn(real_file, failing_operation, at_failure)
        return real_file

    return patch("io.open", open_then_fail)


def _names_in(directory):
    return sorted(entry.name for entry in directory.iterdir())


class TestUploadFileFailedWrite:
    """A failed upload leaves the destination as it was and nothing else behind."""

    UPLOAD_CONTENT = "G0 X1\nG0 X2\nM2\n"

    @pytest.mark.parametrize("failing_operation", ["write", "close"])
    def test_failed_write_leaves_nothing_so_a_retry_succeeds(
        self, file_service, nc_files_dir, mock_grpc_context, failing_operation
    ):
        from linuxcnc_pb import linuxcnc_pb2

        request = linuxcnc_pb2.UploadFileRequest(
            filename="new.ngc", content=self.UPLOAD_CONTENT, fail_if_exists=True
        )
        with _first_write_fails_on(failing_operation):
            response = file_service.UploadFile(request, mock_grpc_context)

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert _names_in(nc_files_dir) == []

        retry_context = MagicMock(spec=grpc.ServicerContext)
        retry_response = file_service.UploadFile(request, retry_context)

        retry_context.abort.assert_not_called()
        assert retry_response.overwritten is False
        assert (nc_files_dir / "new.ngc").read_text() == self.UPLOAD_CONTENT

    @pytest.mark.parametrize("failing_operation", ["write", "close"])
    def test_failed_upload_does_not_remove_a_file_another_request_created(
        self, file_service, nc_files_dir, mock_grpc_context, failing_operation
    ):
        """
        While this upload writes, another request deletes the name and a
        third creates it again. The failing upload must leave that file.
        """
        from linuxcnc_pb import linuxcnc_pb2

        destination = nc_files_dir / "new.ngc"

        def other_requests_delete_and_recreate_the_name():
            destination.unlink(missing_ok=True)
            # os.open, not write_text: io.open is patched during the upload.
            other_file_descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
            )
            os.write(other_file_descriptor, b"the other upload\n")
            os.close(other_file_descriptor)

        with _first_write_fails_on(
            failing_operation, other_requests_delete_and_recreate_the_name
        ):
            response = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="new.ngc", content=self.UPLOAD_CONTENT, fail_if_exists=True
                ),
                mock_grpc_context,
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert _names_in(nc_files_dir) == ["new.ngc"]
        assert destination.read_text() == "the other upload\n"

    @pytest.mark.parametrize("failing_operation", ["write", "close"])
    def test_failed_overwrite_leaves_the_previous_content(
        self, file_service, nc_files_dir, mock_grpc_context, failing_operation
    ):
        from linuxcnc_pb import linuxcnc_pb2

        destination = nc_files_dir / "part.ngc"
        destination.write_text("the previous program\n")

        with _first_write_fails_on(failing_operation):
            response = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="part.ngc", content=self.UPLOAD_CONTENT
                ),
                mock_grpc_context,
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert _names_in(nc_files_dir) == ["part.ngc"]
        assert destination.read_text() == "the previous program\n"

    def test_file_that_another_upload_created_is_not_removed(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Losing the race for a new name must leave the winner's file alone."""
        from linuxcnc_pb import linuxcnc_pb2

        real_mkdir = Path.mkdir

        def mkdir_then_another_upload_wins(directory, *args, **kwargs):
            # mkdir runs between the existence check and the publish step.
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
        assert _names_in(nc_files_dir) == ["new.ngc"]
        assert (nc_files_dir / "new.ngc").read_text() == "the other upload\n"


class TestUploadFilePublish:
    """An upload is written under a temporary name and put in place in one step."""

    @pytest.mark.parametrize("fail_if_exists", [False, True])
    def test_no_temporary_file_remains_after_a_successful_upload(
        self, file_service, nc_files_dir, mock_grpc_context, fail_if_exists
    ):
        from linuxcnc_pb import linuxcnc_pb2

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(
                filename="jobs/part.ngc", content="M2\n", fail_if_exists=fail_if_exists
            ),
            mock_grpc_context,
        )

        mock_grpc_context.abort.assert_not_called()
        assert response.overwritten is False
        assert _names_in(nc_files_dir) == ["jobs"]
        assert _names_in(nc_files_dir / "jobs") == ["part.ngc"]
        assert (nc_files_dir / "jobs" / "part.ngc").read_text() == "M2\n"

    @pytest.mark.parametrize("fail_if_exists", [False, True])
    def test_new_file_has_the_mode_a_plain_write_gives(
        self, file_service, nc_files_dir, mock_grpc_context, fail_if_exists
    ):
        """The same permission bits as before uploads went through a temporary file."""
        from linuxcnc_pb import linuxcnc_pb2

        reference_file = nc_files_dir / "reference.ngc"
        reference_file.write_text("M2\n")

        file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(
                filename="part.ngc", content="M2\n", fail_if_exists=fail_if_exists
            ),
            mock_grpc_context,
        )

        mock_grpc_context.abort.assert_not_called()
        uploaded_mode = stat_module.S_IMODE((nc_files_dir / "part.ngc").stat().st_mode)
        assert uploaded_mode == stat_module.S_IMODE(reference_file.stat().st_mode)

    def test_overwrite_keeps_the_permission_bits_of_the_replaced_file(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        destination = nc_files_dir / "part.ngc"
        destination.write_text("old\n")
        destination.chmod(0o600)

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(filename="part.ngc", content="new\n"),
            mock_grpc_context,
        )

        mock_grpc_context.abort.assert_not_called()
        assert response.overwritten is True
        assert destination.read_text() == "new\n"
        assert stat_module.S_IMODE(destination.stat().st_mode) == 0o600

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root may write a read-only file",
    )
    def test_read_only_file_is_still_not_replaced(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Writing in place failed on a read-only file; the rename must not get round that."""
        from linuxcnc_pb import linuxcnc_pb2

        destination = nc_files_dir / "part.ngc"
        destination.write_text("protected\n")
        destination.chmod(0o444)

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(filename="part.ngc", content="new\n"),
            mock_grpc_context,
        )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert "Permission denied" in mock_grpc_context.abort.call_args[0][1]
        assert _names_in(nc_files_dir) == ["part.ngc"]
        assert destination.read_text() == "protected\n"
        assert stat_module.S_IMODE(destination.stat().st_mode) == 0o444

    def test_overwriting_a_symlinked_name_replaces_the_links_target(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """The name is resolved first, so the link stays and its target is replaced."""
        from linuxcnc_pb import linuxcnc_pb2

        link_target = nc_files_dir / "real.ngc"
        link_target.write_text("old\n")
        (nc_files_dir / "link.ngc").symlink_to(link_target)

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(filename="link.ngc", content="new\n"),
            mock_grpc_context,
        )

        mock_grpc_context.abort.assert_not_called()
        assert response.path == str(link_target)
        assert response.overwritten is True
        assert (nc_files_dir / "link.ngc").is_symlink()
        assert not link_target.is_symlink()
        assert link_target.read_text() == "new\n"
        assert _names_in(nc_files_dir) == ["link.ngc", "real.ngc"]

    def test_link_swapped_in_after_validation_is_replaced_not_followed(
        self, file_service, nc_files_dir, tmp_path, mock_grpc_context
    ):
        """A link put at the destination after the name was checked is not written through."""
        from linuxcnc_pb import linuxcnc_pb2

        file_outside = tmp_path / "outside.txt"
        file_outside.write_text("not to be touched\n")
        destination = nc_files_dir / "part.ngc"
        real_mkdir = Path.mkdir

        def mkdir_then_swap_in_a_link(directory, *args, **kwargs):
            # mkdir runs between the validation and the publish step.
            real_mkdir(directory, *args, **kwargs)
            destination.symlink_to(file_outside)

        with patch.object(Path, "mkdir", mkdir_then_swap_in_a_link):
            response = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(filename="part.ngc", content="M2\n"),
                mock_grpc_context,
            )

        mock_grpc_context.abort.assert_not_called()
        assert response.path == str(destination)
        assert file_outside.read_text() == "not to be touched\n"
        assert not destination.is_symlink()
        assert destination.read_text() == "M2\n"
        assert _names_in(nc_files_dir) == ["part.ngc"]

    def test_dangling_link_swapped_in_refuses_an_exclusive_upload(
        self, file_service, nc_files_dir, tmp_path, mock_grpc_context
    ):
        """With fail_if_exists a link at the destination counts as existing."""
        from linuxcnc_pb import linuxcnc_pb2

        missing_file_outside = tmp_path / "outside.txt"
        destination = nc_files_dir / "part.ngc"
        real_mkdir = Path.mkdir

        def mkdir_then_swap_in_a_link(directory, *args, **kwargs):
            real_mkdir(directory, *args, **kwargs)
            destination.symlink_to(missing_file_outside)

        with patch.object(Path, "mkdir", mkdir_then_swap_in_a_link):
            response = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="part.ngc", content="M2\n", fail_if_exists=True
                ),
                mock_grpc_context,
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.ALREADY_EXISTS
        assert not missing_file_outside.exists()
        assert destination.is_symlink()
        assert _names_in(nc_files_dir) == ["part.ngc"]

    def test_exclusive_upload_is_refused_where_hard_links_are_not_supported(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """Nothing is written rather than risk replacing another request's file."""
        from linuxcnc_pb import linuxcnc_pb2

        def link_not_supported(source_path, destination_path, *args, **kwargs):
            raise OSError(errno.EPERM, "Operation not permitted")

        with patch("os.link", link_not_supported):
            response = file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(
                    filename="part.ngc", content="M2\n", fail_if_exists=True
                ),
                mock_grpc_context,
            )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert "hard links" in mock_grpc_context.abort.call_args[0][1]
        assert _names_in(nc_files_dir) == []

    @pytest.mark.parametrize("directory_name", [".", "jobs"])
    def test_upload_onto_a_directory_is_refused_before_anything_is_written(
        self, file_service, nc_files_dir, mock_grpc_context, directory_name
    ):
        from linuxcnc_pb import linuxcnc_pb2

        (nc_files_dir / "jobs").mkdir()

        response = file_service.UploadFile(
            linuxcnc_pb2.UploadFileRequest(filename=directory_name, content="M2\n"),
            mock_grpc_context,
        )

        assert response is None
        assert _aborted_with(mock_grpc_context) == grpc.StatusCode.INTERNAL
        assert _names_in(nc_files_dir) == ["jobs"]
        assert _names_in(nc_files_dir.parent) == ["nc_files"]


class TestDeleteFileAfterALinkIsSwappedIn:
    """DeleteFile when the file is replaced by a link after the name was checked."""

    def test_the_link_is_removed_and_its_target_is_left(
        self, file_service, nc_files_dir, tmp_path, mock_grpc_context
    ):
        from linuxcnc_pb import linuxcnc_pb2

        file_outside = tmp_path / "outside.txt"
        file_outside.write_text("not to be touched\n")
        destination = nc_files_dir / "part.ngc"
        destination.write_text("M2\n")
        real_is_dir = Path.is_dir

        def is_dir_then_swap_in_a_link(path, *args, **kwargs):
            # The directory check is the last look before the unlink.
            answer = real_is_dir(path, *args, **kwargs)
            destination.unlink()
            destination.symlink_to(file_outside)
            return answer

        with patch.object(Path, "is_dir", is_dir_then_swap_in_a_link):
            response = file_service.DeleteFile(
                linuxcnc_pb2.DeleteFileRequest(filename="part.ngc"), mock_grpc_context
            )

        mock_grpc_context.abort.assert_not_called()
        assert response.path == str(destination)
        assert _names_in(nc_files_dir) == []
        assert file_outside.read_text() == "not to be touched\n"


class TestListFilesAndUploadTemporaryFiles:
    """ListFiles leaves out the temporary files of uploads."""

    def test_leftover_temporary_file_is_not_listed(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """A crash during an upload can leave one behind; it is not a program."""
        from linuxcnc_pb import linuxcnc_pb2
        from linuxcnc_grpc.linuxcnc_service import (
            UPLOAD_TEMPORARY_PREFIX,
            UPLOAD_TEMPORARY_SUFFIX,
        )

        (nc_files_dir / "part.ngc").write_text("M2\n")
        (nc_files_dir / ".hidden.ngc").write_text("M2\n")
        leftover_name = f"{UPLOAD_TEMPORARY_PREFIX}{'0' * 32}{UPLOAD_TEMPORARY_SUFFIX}"
        (nc_files_dir / leftover_name).write_text("G0 X1\n")

        response = file_service.ListFiles(
            linuxcnc_pb2.ListFilesRequest(), mock_grpc_context
        )

        mock_grpc_context.abort.assert_not_called()
        assert [listed_file.name for listed_file in response.files] == [
            ".hidden.ngc",
            "part.ngc",
        ]

    def test_temporary_file_of_an_upload_in_progress_has_the_reserved_name(
        self, file_service, nc_files_dir, mock_grpc_context
    ):
        """The name an upload writes under is one ListFiles leaves out."""
        from linuxcnc_pb import linuxcnc_pb2

        names_seen_while_writing = []
        listings_while_writing = []

        def look_at_the_directory():
            names_seen_while_writing.extend(_names_in(nc_files_dir))
            listings_while_writing.append(
                file_service.ListFiles(
                    linuxcnc_pb2.ListFilesRequest(), MagicMock(spec=grpc.ServicerContext)
                )
            )

        with _first_write_fails_on("write", look_at_the_directory):
            file_service.UploadFile(
                linuxcnc_pb2.UploadFileRequest(filename="part.ngc", content="G0 X1\nM2\n"),
                mock_grpc_context,
            )

        assert len(names_seen_while_writing) == 1
        assert file_service._is_upload_temporary_name(names_seen_while_writing[0])
        assert list(listings_while_writing[0].files) == []
