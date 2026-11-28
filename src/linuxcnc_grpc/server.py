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
# How long serve() lets RPCs in progress finish when it stops the server.
SHUTDOWN_GRACE_PERIOD = 5  # seconds
# Longest serve() waits for the stopped server to report that it is done.
# Longer than the grace period, and never for ever: a transport that never
# finishes stopping must not keep the process from exiting.
SHUTDOWN_WAIT_TIMEOUT = 15.0  # seconds

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


def create_server(
    host: str = "0.0.0.0",
    port: int = 50051,
    max_workers: int = 10,
    nc_files_dir: str | None = None,
    error_channel_mode: str = ERROR_CHANNEL_ON_DEMAND,
    require_linuxcnc: bool = False,
    on_unrecoverable: Callable[[str], None] | None = None,
    command_helper: bool = True,
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
            finds that this process can no longer connect to LinuxCNC, or
            that its command helper process can never be started (see
            LinuxCNCServiceServicer). The server then stays up and
            NOT_SERVING until the process is restarted, so the caller should
            stop it and exit. Called from a background thread; the condition
            is logged as an error whether or not this is given. The call can
            cross with a shutdown: ignore it once you have decided to stop
        command_helper: If True (the default), commands are sent to
            LinuxCNC by a helper process the server starts when it connects,
            so a send that waits for a LinuxCNC task that does not answer
            cannot stall the server. If False, commands are sent from the
            server's own process, as versions up to 1.0 did

    Returns:
        Configured gRPC server (not yet started), with the port bound.

    The port is bound last. If this raises, no port is bound and the
    LinuxCNC servicer, if it was built, is closed; nothing is ever started
    here. A HAL component already created stays until the process exits.

    Start the returned server, or let the process end: gRPC does nothing
    when stop() is called on a server that was never started, so such a
    server keeps its port until the process exits or the object is
    collected.
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

    address = f"{host}:{port}"
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

        # Register services
        linuxcnc_servicer = LinuxCNCServiceServicer(
            nc_files_dir=nc_files_dir,
            error_channel_mode=error_channel_mode,
            require_linuxcnc=require_linuxcnc,
            on_connection_change=report_linuxcnc_connection,
            on_unrecoverable=on_unrecoverable,
            command_helper=command_helper,
        )
        linuxcnc_pb2_grpc.add_LinuxCNCServiceServicer_to_server(linuxcnc_servicer, server)
        hal_servicer = HalServiceServicer()
        hal_pb2_grpc.add_HalServiceServicer_to_server(hal_servicer, server)
        health_servicer.set(HAL_HEALTH_SERVICE, health_pb2.HealthCheckResponse.SERVING)

        # If LinuxCNC is not up yet, keep checking and connect when it is.
        linuxcnc_servicer.start_waiting_for_linuxcnc()

        # The servicer runs background threads (the wait for LinuxCNC and
        # the error channel reader) and the command helper process. Stop
        # them with the server, whoever stops it, so a stopped server does
        # not keep reading LinuxCNC's error channel.
        stop_grpc_server = server.stop

        def stop_server_and_servicer(grace):
            # Health watchers are told first: every service turns
            # NOT_SERVING. Then the transport, and only then the servicer.
            # Closing the servicer takes time (it waits for its lock, its
            # threads and the command helper, each with a limit); the
            # transport's shutdown and its grace period must not start late
            # because of that. Every step runs whatever the others did; the
            # first failure is raised once all three have run.
            failures = []
            stopped = None
            try:
                health_servicer.enter_graceful_shutdown()
            except Exception as health_failure:
                failures.append(health_failure)
            try:
                stopped = stop_grpc_server(grace)
            except Exception as transport_failure:
                failures.append(transport_failure)
            try:
                linuxcnc_servicer.close()
            except Exception as close_failure:
                failures.append(close_failure)
            for later_failure in failures[1:]:
                logger.error(
                    "Another step of stopping the server failed as well",
                    exc_info=later_failure,
                )
            if failures:
                raise failures[0]
            return stopped

        server.stop = stop_server_and_servicer
        logger.info(f"Server configured; binding {address}")

        # Bind last: this is the last statement that can fail. A gRPC server
        # that was never started cannot give its port back (stop() does
        # nothing on it), so the port is only taken once nothing else can
        # fail. A bad address or a port in use fails here, and the cleanup
        # below stops what was built.
        server.add_insecure_port(address)
    except BaseException:
        # No server will be returned, so nothing else would ever stop the
        # LinuxCNC servicer's background threads (in "always" mode the error
        # reader would go on taking LinuxCNC's messages). No port is bound
        # and nothing was started, so there is no transport to stop. A
        # cleanup step that fails is logged: the caller must see the failure
        # that brought us here, not one from the cleanup.
        try:
            health_servicer.enter_graceful_shutdown()
        except Exception:
            logger.exception("Cleaning up after a failed start: the health service failed")
        if linuxcnc_servicer is not None:
            try:
                linuxcnc_servicer.close()
            except Exception:
                logger.exception(
                    "Cleaning up after a failed start: closing the LinuxCNC servicer failed"
                )
        raise

    return server


def serve(
    host: str = "0.0.0.0",
    port: int = 50051,
    nc_files_dir: str | None = None,
    error_channel_mode: str = ERROR_CHANNEL_ON_DEMAND,
    require_linuxcnc: bool = False,
    command_helper: bool = True,
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
        command_helper: Send commands through a helper process (see
            create_server)

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
    # True until the body below has run to its end, so the cleanup knows
    # whether a failure is already on its way to the caller.
    failure_is_propagating = True

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
            command_helper=command_helper,
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
        failure_is_propagating = False
    finally:
        # Also on a failure above (a server.start() that raised included),
        # so the servicer's threads never outlive serve(). If create_server
        # failed there is no server, and it has cleaned up after itself.
        try:
            try:
                if server is not None:
                    stopped = server.stop(grace=SHUTDOWN_GRACE_PERIOD)
                    # Bounded: a transport that never reports having stopped
                    # (one whose start failed half-way, for example) must
                    # not hold serve() for ever.
                    if not stopped.wait(SHUTDOWN_WAIT_TIMEOUT):
                        logger.error(
                            f"The gRPC server did not finish stopping within "
                            f"{SHUTDOWN_WAIT_TIMEOUT:g} seconds; not waiting any longer"
                        )
            finally:
                for shutdown_signal, previous_handler in previous_signal_handlers.items():
                    # None means the previous handler was not set from
                    # Python. It cannot be put back, and leaving this call's
                    # handler would swallow later signals, so the default
                    # action is the closest there is.
                    signal.signal(
                        shutdown_signal,
                        signal.SIG_DFL if previous_handler is None else previous_handler,
                    )
        except Exception:
            # The caller must see the failure that ended serve(), not one
            # from cleaning up after it.
            if not failure_is_propagating:
                raise
            logger.exception("Stopping the server after a failure failed as well")
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
        "--in-process-commands",
        action="store_true",
        help=(
            "Send commands to LinuxCNC from the server's own process, as "
            "versions up to 1.0 did. By default a helper process sends them, "
            "so that a LinuxCNC task that does not answer cannot stall the "
            "whole server for 5 seconds per command"
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
            command_helper=not args.in_process_commands,
        )
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
