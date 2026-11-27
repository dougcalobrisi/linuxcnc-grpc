#!/usr/bin/env python3
"""
Stream Status Example

Demonstrates streaming real-time status updates from the LinuxCNC gRPC server.
This is useful for building dashboards or monitoring applications.

Usage:
    python stream_status.py [--interval 100]

--interval is the time between updates in milliseconds, from 1 to 60000.

Press Ctrl+C to stop streaming. The example gives up, and exits non-zero, when
no update has arrived for 30 seconds or for twice the interval plus 5 seconds,
whichever is longer. It also exits non-zero when the stream fails or the
server cancels it.
"""

import argparse
import sys
import threading
import time

import grpc

# Try installed package first, fall back to local packages/ directory
try:
    from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "packages" / "python"))
    from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc


# The server accepts intervals up to 60 seconds.
MIN_INTERVAL_MS = 1
MAX_INTERVAL_MS = 60000

# A stream has no overall deadline: it runs until Ctrl+C. Instead, give up
# when the server has sent nothing for too long: the larger of
# MIN_STREAM_IDLE_TIMEOUT and STREAM_IDLE_INTERVALS update intervals plus
# STREAM_IDLE_MARGIN, in seconds.
MIN_STREAM_IDLE_TIMEOUT = 30.0
STREAM_IDLE_INTERVALS = 2
STREAM_IDLE_MARGIN = 5.0


def stream_idle_timeout(interval_ms: int) -> float:
    """Seconds without an update after which the stream counts as dead."""
    return max(MIN_STREAM_IDLE_TIMEOUT,
               STREAM_IDLE_INTERVALS * interval_ms / 1000.0 + STREAM_IDLE_MARGIN)


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
    last_update_time = time.monotonic()
    idle_timeout = stream_idle_timeout(interval_ms)
    # Set only when this program cancels the stream itself.
    stream_went_silent = threading.Event()
    stream_failed = False

    status_stream = stub.StreamStatus(request)

    def cancel_stream_when_silent():
        while status_stream.is_active():
            if time.monotonic() - last_update_time > idle_timeout:
                stream_went_silent.set()
                status_stream.cancel()
                return
            time.sleep(1.0)

    threading.Thread(target=cancel_stream_when_silent, daemon=True).start()

    try:
        for status in status_stream:
            update_count += 1
            last_update_time = time.monotonic()
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
        # CANCELLED is only expected when the watchdog above cancelled the
        # stream (reported below). Ctrl+C arrives as KeyboardInterrupt, so any
        # other CANCELLED came from the server and is a failure like the rest.
        if not (e.code() == grpc.StatusCode.CANCELLED and stream_went_silent.is_set()):
            print(f"\ngRPC error: {e.code()}: {e.details()}", file=sys.stderr)
            stream_failed = True
    except KeyboardInterrupt:
        pass
    finally:
        status_stream.cancel()
        channel.close()

    # Print summary
    elapsed = time.time() - start_time
    print(f"\n\nReceived {update_count} updates in {elapsed:.1f}s "
          f"({update_count/elapsed:.1f} updates/sec)")

    if stream_went_silent.is_set():
        print(f"No status update for {idle_timeout:g} seconds; giving up.",
              file=sys.stderr)
        sys.exit(1)
    if stream_failed:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Stream LinuxCNC status via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")
    parser.add_argument("--interval", type=int, default=100,
                        help="Update interval in milliseconds, 1 to 60000 (default: 100). "
                             "The example gives up when no update arrives for 30 seconds "
                             "or twice the interval plus 5 seconds, whichever is longer")
    args = parser.parse_args()

    if not MIN_INTERVAL_MS <= args.interval <= MAX_INTERVAL_MS:
        print("Error: --interval must be a whole number of milliseconds from 1 to 60000",
              file=sys.stderr)
        sys.exit(1)

    stream_status(args.host, args.port, args.interval)


if __name__ == "__main__":
    main()
