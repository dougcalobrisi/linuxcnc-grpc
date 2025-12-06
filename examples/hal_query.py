#!/usr/bin/env python3
"""
HAL Query Example

Query HAL (Hardware Abstraction Layer) pins, signals, and parameters.
Useful for debugging HAL configurations and monitoring I/O.

Usage:
    python hal_query.py pins "axis.*"
    python hal_query.py signals
    python hal_query.py components
    python hal_query.py watch "spindle.0.speed-out" "axis.x.pos-cmd"
"""

import argparse
import sys
import time

import grpc

# Try installed package first, fall back to local src/ directory
try:
    from linuxcnc_grpc_server._generated import hal_pb2
    from linuxcnc_grpc_server._generated import hal_pb2_grpc
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from linuxcnc_grpc_server._generated import hal_pb2
    from linuxcnc_grpc_server._generated import hal_pb2_grpc


def format_value(value: hal_pb2.HalValue) -> str:
    """Format a HAL value for display."""
    which = value.WhichOneof("value")
    if which == "bit_value":
        return "TRUE" if value.bit_value else "FALSE"
    elif which == "float_value":
        return f"{value.float_value:.6g}"
    elif which == "s32_value":
        return str(value.s32_value)
    elif which == "u32_value":
        return str(value.u32_value)
    elif which == "s64_value":
        return str(value.s64_value)
    elif which == "u64_value":
        return str(value.u64_value)
    return "?"


def format_type(hal_type: hal_pb2.HalType) -> str:
    """Format HAL type for display."""
    return hal_pb2.HalType.Name(hal_type).replace("HAL_", "")


def format_direction(direction) -> str:
    """Format pin/param direction for display."""
    name = hal_pb2.PinDirection.Name(direction) if hasattr(direction, 'DESCRIPTOR') else str(direction)
    return name.replace("HAL_", "").replace("PIN_DIR_", "")


def query_pins(stub: hal_pb2_grpc.HalServiceStub, pattern: str = "*"):
    """Query and display HAL pins matching a pattern."""
    request = hal_pb2.QueryPinsCommand()
    request.pattern = pattern

    response = stub.QueryPins(request)
    if not response.success:
        print(f"Error: {response.error}", file=sys.stderr)
        return

    print(f"Found {len(response.pins)} pins matching '{pattern}':\n")
    print(f"{'Name':<50} {'Type':<6} {'Dir':<4} {'Value':<15} {'Signal'}")
    print("-" * 90)

    for pin in sorted(response.pins, key=lambda p: p.name):
        direction = format_direction(pin.direction)
        value = format_value(pin.value)
        pin_type = format_type(pin.type)
        signal = pin.signal or "-"
        print(f"{pin.name:<50} {pin_type:<6} {direction:<4} {value:<15} {signal}")


def query_signals(stub: hal_pb2_grpc.HalServiceStub, pattern: str = "*"):
    """Query and display HAL signals matching a pattern."""
    request = hal_pb2.QuerySignalsCommand()
    request.pattern = pattern

    response = stub.QuerySignals(request)
    if not response.success:
        print(f"Error: {response.error}", file=sys.stderr)
        return

    print(f"Found {len(response.signals)} signals matching '{pattern}':\n")
    print(f"{'Name':<40} {'Type':<6} {'Value':<15} {'Driver':<30} Readers")
    print("-" * 100)

    for sig in sorted(response.signals, key=lambda s: s.name):
        value = format_value(sig.value)
        sig_type = format_type(sig.type)
        driver = sig.driver or "(none)"
        readers = f"{sig.reader_count} readers" if sig.reader_count else "-"
        print(f"{sig.name:<40} {sig_type:<6} {value:<15} {driver:<30} {readers}")


def query_params(stub: hal_pb2_grpc.HalServiceStub, pattern: str = "*"):
    """Query and display HAL parameters matching a pattern."""
    request = hal_pb2.QueryParamsCommand()
    request.pattern = pattern

    response = stub.QueryParams(request)
    if not response.success:
        print(f"Error: {response.error}", file=sys.stderr)
        return

    print(f"Found {len(response.params)} parameters matching '{pattern}':\n")
    print(f"{'Name':<50} {'Type':<6} {'Mode':<4} {'Value'}")
    print("-" * 80)

    for param in sorted(response.params, key=lambda p: p.name):
        value = format_value(param.value)
        param_type = format_type(param.type)
        mode = "RW" if param.direction == hal_pb2.HAL_RW else "RO"
        print(f"{param.name:<50} {param_type:<6} {mode:<4} {value}")


def query_components(stub: hal_pb2_grpc.HalServiceStub, pattern: str = "*"):
    """Query and display HAL components matching a pattern."""
    request = hal_pb2.QueryComponentsCommand()
    request.pattern = pattern

    response = stub.QueryComponents(request)
    if not response.success:
        print(f"Error: {response.error}", file=sys.stderr)
        return

    print(f"Found {len(response.components)} components matching '{pattern}':\n")
    print(f"{'Name':<30} {'ID':<6} {'Ready':<6} {'Pins':<6} {'Params'}")
    print("-" * 60)

    for comp in sorted(response.components, key=lambda c: c.name):
        ready = "Yes" if comp.ready else "No"
        print(f"{comp.name:<30} {comp.id:<6} {ready:<6} {len(comp.pins):<6} {len(comp.params)}")


def watch_values(stub: hal_pb2_grpc.HalServiceStub, names: list[str], interval: float = 0.5):
    """Watch HAL values for changes."""
    request = hal_pb2.WatchRequest()
    request.names.extend(names)
    request.interval = interval

    print(f"Watching {len(names)} values (interval: {interval}s)")
    print("Press Ctrl+C to stop\n")

    try:
        for batch in stub.WatchValues(request):
            for change in batch.changes:
                old_val = format_value(change.old_value)
                new_val = format_value(change.new_value)
                ts = time.strftime("%H:%M:%S", time.localtime(change.timestamp / 1e9))
                print(f"[{ts}] {change.name}: {old_val} -> {new_val}")

    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.CANCELLED:
            pass
        else:
            print(f"\ngRPC error: {e.code()}: {e.details()}", file=sys.stderr)
            sys.exit(1)
    except KeyboardInterrupt:
        pass


def get_system_status(stub: hal_pb2_grpc.HalServiceStub):
    """Get and display overall HAL system status."""
    request = hal_pb2.GetSystemStatusRequest()
    status = stub.GetSystemStatus(request)

    print("HAL System Status")
    print("=" * 40)
    print(f"Pins:       {len(status.pins)}")
    print(f"Signals:    {len(status.signals)}")
    print(f"Parameters: {len(status.params)}")
    print(f"Components: {len(status.components)}")
    print(f"Simulation: {status.is_sim}")
    print(f"Real-time:  {status.is_rt}")
    print(f"Userspace:  {status.is_userspace}")
    if status.kernel_version:
        print(f"Kernel:     {status.kernel_version}")


def main():
    parser = argparse.ArgumentParser(description="Query HAL via gRPC")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", type=int, default=50051, help="gRPC server port")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Subcommands
    pins_parser = subparsers.add_parser("pins", help="Query HAL pins")
    pins_parser.add_argument("pattern", nargs="?", default="*", help="Glob pattern")

    signals_parser = subparsers.add_parser("signals", help="Query HAL signals")
    signals_parser.add_argument("pattern", nargs="?", default="*", help="Glob pattern")

    params_parser = subparsers.add_parser("params", help="Query HAL parameters")
    params_parser.add_argument("pattern", nargs="?", default="*", help="Glob pattern")

    comps_parser = subparsers.add_parser("components", help="Query HAL components")
    comps_parser.add_argument("pattern", nargs="?", default="*", help="Glob pattern")

    watch_parser = subparsers.add_parser("watch", help="Watch values for changes")
    watch_parser.add_argument("names", nargs="+", help="Pin/signal/param names to watch")
    watch_parser.add_argument("--interval", type=float, default=0.5, help="Update interval")

    subparsers.add_parser("status", help="Get HAL system status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        print("\nExamples:")
        print('  python hal_query.py pins "axis.*"')
        print('  python hal_query.py signals')
        print('  python hal_query.py components')
        print('  python hal_query.py watch spindle.0.speed-out axis.x.pos-cmd')
        sys.exit(1)

    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = hal_pb2_grpc.HalServiceStub(channel)

    try:
        if args.command == "pins":
            query_pins(stub, args.pattern)
        elif args.command == "signals":
            query_signals(stub, args.pattern)
        elif args.command == "params":
            query_params(stub, args.pattern)
        elif args.command == "components":
            query_components(stub, args.pattern)
        elif args.command == "watch":
            watch_values(stub, args.names, args.interval)
        elif args.command == "status":
            get_system_status(stub)

    except grpc.RpcError as e:
        print(f"gRPC error: {e.code()}: {e.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        channel.close()


if __name__ == "__main__":
    main()
