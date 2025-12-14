#!/usr/bin/env python3
"""
Stream Status Example

Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
This is useful for building dashboards or monitoring applications.

Usage:
    python stream_status.py [--interval 100]

Press Ctrl+C to stop streaming.
"""

import argparse
import sys
import time

import grpc

# Try installed package first, fall back to local src/ directory
try:
    from linuxcnc_grpc._generated import linuxcnc_pb2
    from linuxcnc_grpc._generated import linuxcnc_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
    from linuxcnc_grpc._generated import linuxcnc_pb2
    from linuxcnc_grpc._generated import linuxcnc_pb2_grpc


def format_position(pos) -> str:
    """Format a position for display."""
    return f"X:{pos.x:8.3f} Y:{pos.y:8.3f} Z:{pos.z:8.3f}"


def format_state(status) -> str:
    """Format machine state for display."""
    mode = linuxcnc_pb2.TaskMode.Name(status.task.task_mode).replace("MODE_", "")
    state = linuxcnc_pb2.TaskState.Name(status.task.task_state).replace("STATE_", "")
    interp = linuxcnc_pb2.InterpState.Name(status.task.interp_state).replace("INTERP_", "")
    return f"{mode}/{state}/{interp}"


def stream_status(host: str, port: int, interval_ms: int):
    """Stream status updates and display them."""
    channel = grpc.insecure_channel(f"{host}:{port}")
    stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

    request = linuxcnc_pb2.StreamStatusRequest()
    request.interval_ms = interval_ms

    print(f"Streaming status from {host}:{port} (interval: {interval_ms}ms)")
    print("Press Ctrl+C to stop\n")
    print("-" * 80)

    update_count = 0
    start_time = time.time()

    try:
        for status in stub.StreamStatus(request):
            update_count += 1
            elapsed = time.time() - start_time
            rate = update_count / elapsed if elapsed > 0 else 0

            # Clear line and print status
            pos = format_position(status.position.actual_position)
            state = format_state(status)
            vel = status.trajectory.current_vel
            feed = status.trajectory.feedrate * 100

            # Format spindle info
            spindle_info = ""
            if status.spindles:
                s = status.spindles[0]
                if s.speed > 0:
                    spindle_info = f" S:{s.speed:.0f}"

            print(f"\r[{update_count:6d}] {pos} | {state:20s} | "
                  f"V:{vel:7.2f} F:{feed:5.1f}%{spindle_info}  ",
                  end="", flush=True)

    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.CANCELLED:
            pass  # Normal cancellation
        else:
            print(f"\ngRPC error: {e.code()}: {e.details()}", file=sys.stderr)
            sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()

    # Print summary
    elapsed = time.time() - start_time
    print(f"\n\nReceived {update_count} updates in {elapsed:.1f}s "
          f"({update_count/elapsed:.1f} updates/sec)")


def main():
    parser = argparse.ArgumentParser(description="Stream LinuxCNC status via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("--interval", type=int, default=100,
                        help="Update interval in milliseconds (default: 100)")
    args = parser.parse_args()

    stream_status(args.host, args.port, args.interval)


if __name__ == "__main__":
    main()
