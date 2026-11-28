#!/usr/bin/env python3
"""
Upload File Example

Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
lists files to confirm, and optionally cleans up.

The upload never replaces a file: if the name is already taken the example
stops, and --cleanup only deletes a file this run created.

Usage:
    python upload_file.py [--host HOST] [--port PORT] [--filename NAME] [--cleanup]
"""

import argparse
import sys

import grpc

# Try installed package first, fall back to local packages/ directory
try:
    from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "packages" / "python"))
    from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc


# Sample G-code program
SAMPLE_GCODE = """\
(Sample G-code uploaded via gRPC)
G21 (metric)
G90 (absolute positioning)
G0 Z5
G0 X0 Y0
G1 Z-1 F100
G1 X50 F200
G1 Y50
G1 X0
G1 Y0
G0 Z5
M2
"""


RPC_TIMEOUT = 10.0  # seconds, for every RPC


def upload_file(host: str, port: int, filename: str, cleanup: bool) -> None:
    """Upload a G-code file, list files, and optionally clean up."""
    channel = grpc.insecure_channel(f"{host}:{port}")
    stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

    # True only once this run has created the file, so that --cleanup can
    # never delete a file that was there before.
    file_was_created = False

    try:
        # Upload the file. fail_if_exists makes the server refuse to replace
        # an existing file instead of overwriting it.
        print(f"Uploading '{filename}'...")
        upload_request = linuxcnc_pb2.UploadFileRequest(
            filename=filename,
            content=SAMPLE_GCODE,
            fail_if_exists=True,
        )
        try:
            upload_response = stub.UploadFile(upload_request, timeout=RPC_TIMEOUT)
        except grpc.RpcError as rpc_error:
            if rpc_error.code() == grpc.StatusCode.ALREADY_EXISTS:
                print(f"'{filename}' already exists on the machine and was left "
                      "untouched. Delete it there or pick another name with "
                      "--filename.", file=sys.stderr)
                sys.exit(1)
            raise
        file_was_created = True
        print(f"  Written to: {upload_response.path}")
        print(f"  Size: {len(SAMPLE_GCODE)} bytes")

        # List files to confirm
        print("\nListing files...")
        list_response = stub.ListFiles(linuxcnc_pb2.ListFilesRequest(), timeout=RPC_TIMEOUT)
        print(f"  Directory: {list_response.directory}")
        print(f"  {'Name':<30} {'Size':>8}  {'Type'}")
        print(f"  {'-'*30} {'-'*8}  {'-'*4}")
        for file_info in list_response.files:
            file_type = "DIR" if file_info.is_directory else "FILE"
            print(f"  {file_info.name:<30} {file_info.size_bytes:>8}  {file_type}")

    except grpc.RpcError as rpc_error:
        print(f"gRPC error: {rpc_error.code()}: {rpc_error.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            # Optionally clean up, but only the file this run created.
            if cleanup and file_was_created:
                print(f"\nDeleting '{filename}'...")
                delete_request = linuxcnc_pb2.DeleteFileRequest(filename=filename)
                delete_response = stub.DeleteFile(delete_request, timeout=RPC_TIMEOUT)
                print(f"  Deleted: {delete_response.path}")
        except grpc.RpcError as rpc_error:
            print(f"Could not delete '{filename}': {rpc_error.code()}: "
                  f"{rpc_error.details()}", file=sys.stderr)
            sys.exit(1)
        finally:
            channel.close()


def main():
    parser = argparse.ArgumentParser(description="Upload G-code file via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("--filename", default="grpc_example.ngc",
                        help="Name to upload as (default: grpc_example.ngc)")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete the uploaded file afterwards")
    args = parser.parse_args()

    upload_file(args.host, args.port, args.filename, args.cleanup)


if __name__ == "__main__":
    main()
