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
import sys
from concurrent import futures

import grpc

from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from linuxcnc_pb import linuxcnc_pb2_grpc, hal_pb2_grpc
from .linuxcnc_service import LinuxCNCServiceServicer
from .hal_service import HalServiceServicer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class _RequestLoggingInterceptor(grpc.ServerInterceptor):
    """Logs all incoming RPC calls at debug level."""

    def intercept_service(self, continuation, handler_call_details):
        logger.debug(f"RPC: {handler_call_details.method}")
        return continuation(handler_call_details)


def create_server(host: str = "0.0.0.0", port: int = 50051, max_workers: int = 10) -> grpc.Server:
    """
    Create and configure the gRPC server.

    Args:
        host: Host address to bind to
        port: Port number to listen on
        max_workers: Maximum number of worker threads

    Returns:
        Configured gRPC server (not yet started)
    """
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=[_RequestLoggingInterceptor()],
        options=[
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 10000),
            ('grpc.max_receive_message_length', 10 * 1024 * 1024),  # 10MB
        ]
    )

    # Register services
    linuxcnc_pb2_grpc.add_LinuxCNCServiceServicer_to_server(
        LinuxCNCServiceServicer(),
        server
    )
    hal_pb2_grpc.add_HalServiceServicer_to_server(
        HalServiceServicer(),
        server
    )

    # Register health check service
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("linuxcnc.LinuxCNCService", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("hal.HalService", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    # Add insecure port (for development/testing)
    address = f"{host}:{port}"
    server.add_insecure_port(address)

    logger.info(f"Server configured on {address}")
    return server


def serve(host: str = "0.0.0.0", port: int = 50051):
    """
    Start the gRPC server and run until interrupted.

    Args:
        host: Host address to bind to
        port: Port number to listen on
    """
    server = create_server(host, port)

    # Handle shutdown signals gracefully
    shutdown_event = False

    def signal_handler(signum, frame):
        nonlocal shutdown_event
        if not shutdown_event:
            shutdown_event = True
            logger.info("Shutdown signal received, stopping server...")
            server.stop(grace=5)  # 5 second grace period

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start server
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

    # Wait for termination
    server.wait_for_termination()
    logger.info("Server stopped")


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
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    try:
        serve(host=args.host, port=args.port)
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
