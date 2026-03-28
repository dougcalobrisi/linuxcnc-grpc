#!/usr/bin/env python3
"""
Upload File Example

Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
lists files to confirm, and optionally cleans up.

Usage:
    python upload_file.py [--host HOST] [--port PORT] [--cleanup]
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


def upload_file(host: str, port: int, cleanup: bool) -> None:
    """Upload a G-code file, list files, and optionally clean up."""
    channel = grpc.insecure_channel(f"{host}:{port}")
    stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

    filename = "grpc_example.ngc"

    try:
        # Upload the file
        print(f"Uploading '{filename}'...")
        upload_request = linuxcnc_pb2.UploadFileRequest(
            filename=filename,
            content=SAMPLE_GCODE,
        )
        upload_response = stub.UploadFile(upload_request)
        overwrite_msg = " (overwritten)" if upload_response.overwritten else ""
        print(f"  Written to: {upload_response.path}{overwrite_msg}")
        print(f"  Size: {len(SAMPLE_GCODE)} bytes")

        # List files to confirm
        print(f"\nListing files...")
        list_request = linuxcnc_pb2.ListFilesRequest()
        list_response = stub.ListFiles(list_request)
        print(f"  Directory: {list_response.directory}")
        print(f"  {'Name':<30} {'Size':>8}  {'Type'}")
        print(f"  {'-'*30} {'-'*8}  {'-'*4}")
        for f in list_response.files:
            ftype = "DIR" if f.is_directory else "FILE"
            print(f"  {f.name:<30} {f.size_bytes:>8}  {ftype}")

        # Optionally clean up
        if cleanup:
            print(f"\nDeleting '{filename}'...")
            delete_request = linuxcnc_pb2.DeleteFileRequest(filename=filename)
            delete_response = stub.DeleteFile(delete_request)
            print(f"  Deleted: {delete_response.path}")

    except grpc.RpcError as e:
        print(f"gRPC error: {e.code()}: {e.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        channel.close()


def main():
    parser = argparse.ArgumentParser(description="Upload G-code file via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("--cleanup", action="store_true", help="Delete the file after uploading")
    args = parser.parse_args()

    upload_file(args.host, args.port, args.cleanup)


if __name__ == "__main__":
    main()
