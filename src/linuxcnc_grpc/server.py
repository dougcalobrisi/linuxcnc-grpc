#!/usr/bin/env python3
"""
LinuxCNC + HAL gRPC Server

A combined gRPC server implementing both LinuxCNCService and HalService.

Usage:
    python -m linuxcnc_grpc.server [--port PORT] [--host HOST]
    linuxcnc-grpc [--port PORT] [--host HOST]

Example:
    linuxcnc-grpc --port 50051 --host 0.0.0.0
"""

import argparse
import logging
import signal
import time
import sys
from concurrent import futures
from typing import Callable

import grpc

from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from linuxcnc_pb import linuxcnc_pb2_grpc, hal_pb2_grpc
from .linuxcnc_service import MAX_UPLOAD_SIZE, LinuxCNCServiceServicer
from .error_broker import ERROR_CHANNEL_MODES, ERROR_CHANNEL_ON_DEMAND
from .hal_service import HalServiceServicer

# Upload cap plus 1 MB for the filename and protobuf framing.
MAX_RECEIVE_MESSAGE_BYTES = MAX_UPLOAD_SIZE + 1024 * 1024

# Names the health service reports on, besides "" for the server as a whole.
LINUXCNC_HEALTH_SERVICE = "linuxcnc.LinuxCNCService"
HAL_HEALTH_SERVICE = "hal.HalService"
# How often serve() looks whether a shutdown signal has arrived.
SHUTDOWN_POLL_INTERVAL = 0.2  # seconds
# Longest create_server waits for the transport of a failed start to go away.
FAILED_START_STOP_TIMEOUT = 5.0  # seconds

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class UnrecoverableConnectionError(RuntimeError):
    """
    Raised by serve() after it has stopped the server because this process
    can no longer connect to LinuxCNC. Only a new process can.
    """


class _RequestLoggingInterceptor(grpc.ServerInterceptor):
    """Logs all incoming RPC calls at debug level."""

    def intercept_service(self, continuation, handler_call_details):
        logger.debug(f"RPC: {handler_call_details.method}")
        return continuation(handler_call_details)


def _release_port_of_unstarted_server(server: grpc.Server) -> None:
    """
    Free the port of a gRPC server that was bound but never started.

    stop() does nothing on a server that was never started, and dropping the
    object does not free the port either (measured with grpcio 1.76): the
    port then stays bound for the life of the process. Starting the server
    and stopping it at once does free it. The caller makes sure that only
    the health service is registered at this point.
    """
    try:
        server.start()
        server.stop(0).wait(FAILED_START_STOP_TIMEOUT)
    except Exception as e:
        logger.error(f"Could not release the server's port after a failed start: {e}")


def create_server(
    host: str = "0.0.0.0",
    port: int = 50051,
    max_workers: int = 10,
    nc_files_dir: str | None = None,
    error_channel_mode: str = ERROR_CHANNEL_ON_DEMAND,
    require_linuxcnc: bool = False,
    on_unrecoverable: Callable[[str], None] | None = None,
) -> grpc.Server:
    """
    Create and configure the gRPC server.

    Args:
        host: Host address to bind to
        port: Port number to listen on
        max_workers: Maximum number of worker threads
        nc_files_dir: Override directory for NC files
        error_channel_mode: When the server reads LinuxCNC's error channel:
            "on-demand" (only while a StreamErrors client is connected),
            "always" or "off"
        require_linuxcnc: If True, raise when LinuxCNC is not running (the
            behaviour up to 1.0). If False, start anyway: the health service
            reports NOT_SERVING and LinuxCNC RPCs return UNAVAILABLE until
            the server has connected
        on_unrecoverable: Called once, with a description, if the server
            finds that this process can no longer connect to LinuxCNC (see
            LinuxCNCServiceServicer). The server then stays up and
            NOT_SERVING until the process is restarted, so the caller should
            stop it and exit. Called from a background thread; the condition
            is logged as an error whether or not this is given

    Returns:
        Configured gRPC server (not yet started). If this raises after the
        port was bound, the port is released and the LinuxCNC servicer is
        closed. The HAL component, if it was created, stays until the
        process exits.
    """
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=[_RequestLoggingInterceptor()],
        options=[
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 10000),
            # Headroom above the 10 MB upload cap, so a too-large or near-limit upload reaches
            # UploadFile's own size check (a clear INVALID_ARGUMENT) instead of being rejected
            # by the transport with a bare RESOURCE_EXHAUSTED.
            ('grpc.max_receive_message_length', MAX_RECEIVE_MESSAGE_BYTES),
        ]
    )

    # Bind first: a bad address or a port in use fails here, before any
    # servicer has started a background thread.
    address = f"{host}:{port}"
    server.add_insecure_port(address)

    # From here on the server holds the port, so every failure has to give
    # it back and stop whatever servicer was built.
    health_servicer = health.HealthServicer()
    linuxcnc_servicer = None
    try:
        # Health check service. The overall status ("") and the LinuxCNC
        # service follow the connection to LinuxCNC; they start as
        # NOT_SERVING and the servicer reports when it connects.
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

        def report_linuxcnc_connection(connected: bool) -> None:
            serving_status = (
                health_pb2.HealthCheckResponse.SERVING if connected
                else health_pb2.HealthCheckResponse.NOT_SERVING
            )
            health_servicer.set(LINUXCNC_HEALTH_SERVICE, serving_status)
            health_servicer.set("", serving_status)
            logger.info(f"LinuxCNC connection: {'up' if connected else 'down'}")

        health_servicer.set(LINUXCNC_HEALTH_SERVICE, health_pb2.HealthCheckResponse.NOT_SERVING)
        health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)

        linuxcnc_servicer = LinuxCNCServiceServicer(
            nc_files_dir=nc_files_dir,
            error_channel_mode=error_channel_mode,
            require_linuxcnc=require_linuxcnc,
            on_connection_change=report_linuxcnc_connection,
            on_unrecoverable=on_unrecoverable,
        )
        hal_servicer = HalServiceServicer()

        # If LinuxCNC is not up yet, keep checking and connect when it is.
        linuxcnc_servicer.start_waiting_for_linuxcnc()

        # Registered only now that nothing else can fail: releasing the port
        # after a failure starts the server for a moment, and it must not
        # offer the LinuxCNC or HAL service then.
        linuxcnc_pb2_grpc.add_LinuxCNCServiceServicer_to_server(linuxcnc_servicer, server)
        hal_pb2_grpc.add_HalServiceServicer_to_server(hal_servicer, server)
        health_servicer.set(HAL_HEALTH_SERVICE, health_pb2.HealthCheckResponse.SERVING)
    except BaseException:
        # No server will be returned, so nothing else would ever free the
        # port or stop the LinuxCNC servicer's background threads (in
        # "always" mode the error reader would go on taking LinuxCNC's
        # messages).
        try:
            health_servicer.enter_graceful_shutdown()
            if linuxcnc_servicer is not None:
                linuxcnc_servicer.close()
        finally:
            _release_port_of_unstarted_server(server)
        raise

    # The servicer runs background threads (the wait for LinuxCNC and the
    # error channel reader). Stop them with the server, whoever stops it, so
    # a stopped server does not keep reading LinuxCNC's error channel.
    stop_grpc_server = server.stop

    def stop_server_and_servicer(grace):
        # Tell health watchers first: every service turns NOT_SERVING.
        health_servicer.enter_graceful_shutdown()
        # Then the transport, and only then the servicer. Closing the
        # servicer can wait for its lock, which a command that LinuxCNC is
        # not answering holds for seconds; the transport's shutdown and its
        # grace period must not start late because of that.
        try:
            return stop_grpc_server(grace)
        finally:
            linuxcnc_servicer.close()

    server.stop = stop_server_and_servicer

    logger.info(f"Server configured on {address}")
    return server


def serve(
    host: str = "0.0.0.0",
    port: int = 50051,
    nc_files_dir: str | None = None,
    error_channel_mode: str = ERROR_CHANNEL_ON_DEMAND,
    require_linuxcnc: bool = False,
):
    """
    Start the gRPC server and run until interrupted.

    Args:
        host: Host address to bind to
        port: Port number to listen on
        nc_files_dir: Override directory for NC files
        error_channel_mode: When the server reads LinuxCNC's error channel
            ("on-demand", "always" or "off")
        require_linuxcnc: Fail instead of waiting if LinuxCNC is not running

    Raises:
        UnrecoverableConnectionError: after stopping the server, if this
            process can no longer connect to LinuxCNC and has to be
            restarted.
    """
    # Handle shutdown signals gracefully. The handler only records the
    # request: it runs in the main thread between two instructions of whatever
    # that thread was doing, so anything that takes a lock there (stopping the
    # server, logging, even threading.Event.set) can deadlock against a lock
    # the interrupted code already holds.
    shutdown_signals_received = []

    def signal_handler(signum, frame):
        shutdown_signals_received.append(signum)

    # Filled in from the thread that waits for LinuxCNC, if it finds that
    # this process can no longer connect. Like the signal handler it only
    # records; the loop below acts on it.
    unrecoverable_errors = []

    def record_unrecoverable_error(error_text):
        unrecoverable_errors.append(error_text)

    # What was installed before, to put back when serve() ends: a signal
    # arriving later must not land in this call's list, where nobody looks.
    previous_signal_handlers = {}
    server = None

    try:
        # Installed before the server is created, which can take as long as
        # a check for LinuxCNC: a signal during that time is recorded and
        # ends the loop below on its first pass, with a normal stop.
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            previous_signal_handlers[shutdown_signal] = signal.signal(
                shutdown_signal, signal_handler
            )

        server = create_server(
            host,
            port,
            nc_files_dir=nc_files_dir,
            error_channel_mode=error_channel_mode,
            require_linuxcnc=require_linuxcnc,
            on_unrecoverable=record_unrecoverable_error,
        )

        server.start()
        logger.info("=" * 60)
        logger.info("LinuxCNC + HAL gRPC Server")
        logger.info("=" * 60)
        logger.info(f"Listening on {host}:{port}")
        logger.info("")
        logger.info("Services available:")
        logger.info("  - LinuxCNCService (linuxcnc.proto)")
        logger.info("  - HalService (hal.proto)")
        logger.info("  - Health (grpc.health.v1)")
        logger.info("")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 60)

        # Wait for a shutdown signal. Polling, because the handler must not
        # touch anything the waiting code could be holding.
        while not shutdown_signals_received and not unrecoverable_errors:
            time.sleep(SHUTDOWN_POLL_INTERVAL)
        if unrecoverable_errors:
            logger.error(
                "This process can no longer connect to LinuxCNC and must be "
                "restarted; stopping server..."
            )
        else:
            logger.info("Shutdown signal received, stopping server...")
    finally:
        # Also on a failure above, so the servicer's threads never outlive
        # serve(). 5 second grace period for RPCs in progress. If
        # create_server failed there is no server, and it has cleaned up
        # after itself.
        try:
            if server is not None:
                server.stop(grace=5).wait()
        finally:
            for shutdown_signal, previous_handler in previous_signal_handlers.items():
                # None means the previous handler was not set from Python.
                # It cannot be put back, and leaving this call's handler
                # would swallow later signals, so the default action is the
                # closest there is.
                signal.signal(
                    shutdown_signal,
                    signal.SIG_DFL if previous_handler is None else previous_handler,
                )
    logger.info("Server stopped")
    if unrecoverable_errors:
        # main() turns this into exit status 1, so a supervisor restarts
        # the process.
        raise UnrecoverableConnectionError(
            "This process can no longer connect to LinuxCNC and must be "
            f"restarted: {unrecoverable_errors[0]}"
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="LinuxCNC + HAL gRPC Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host address to bind to"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=50051,
        help="Port number to listen on"
    )
    parser.add_argument(
        "--nc-files",
        default=None,
        help="Directory for NC files (default: $LINUXCNC_NC_FILES or /home/linuxcnc/linuxcnc/nc_files)"
    )
    parser.add_argument(
        "--error-channel",
        choices=ERROR_CHANNEL_MODES,
        default=ERROR_CHANNEL_ON_DEMAND,
        help=(
            "When to read LinuxCNC's error channel. Messages the server reads are "
            "not shown by the machine's GUI. on-demand: only while a StreamErrors "
            "client is connected. always: from startup, so status and late "
            "subscribers get recent messages. off: never; StreamErrors is refused"
        ),
    )
    parser.add_argument(
        "--require-linuxcnc",
        action="store_true",
        help=(
            "Exit with an error if LinuxCNC is not running at startup, as "
            "versions up to 1.0 did. By default the server starts anyway and "
            "connects when LinuxCNC comes up"
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    try:
        serve(
            host=args.host,
            port=args.port,
            nc_files_dir=args.nc_files,
            error_channel_mode=args.error_channel,
            require_linuxcnc=args.require_linuxcnc,
        )
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
