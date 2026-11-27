"""LinuxCNC gRPC Server - Remote machine control via gRPC.

This package provides both the gRPC server implementation and re-exports all
protobuf types for convenient client-side usage.

Server Usage (requires LinuxCNC environment):
    from linuxcnc_grpc import serve
    serve(host="127.0.0.1", port=50051)  # runs until interrupted

    # Or build the server and manage its lifetime yourself:
    from linuxcnc_grpc import create_server
    server = create_server(host="127.0.0.1", port=50051)
    server.start()
    try:
        server.wait_for_termination()
    finally:
        server.stop(grace=5)  # also stops the server's background threads

Client Usage (works anywhere):
    from linuxcnc_grpc import (
        LinuxCNCStatus, LinuxCNCCommand, StateCommand,
        LinuxCNCServiceStub, HalServiceStub,
    )

Direct proto access:
    from linuxcnc_pb import linuxcnc_pb2, hal_pb2
"""

__version__ = "1.0.0"

# =============================================================================
# RE-EXPORT ALL PROTOBUF TYPES FROM linuxcnc_pb
# =============================================================================

# Import all types from the generated package
from linuxcnc_pb import *

# Also export the modules for direct access
from linuxcnc_pb import linuxcnc_pb2
from linuxcnc_pb import linuxcnc_pb2_grpc
from linuxcnc_pb import hal_pb2
from linuxcnc_pb import hal_pb2_grpc

# =============================================================================
# SERVER COMPONENTS (lazy-loaded - require LinuxCNC environment)
# =============================================================================


def __getattr__(name):
    """Lazy load server components only when accessed."""
    if name == "create_server":
        from .server import create_server
        return create_server
    elif name == "serve":
        from .server import serve
        return serve
    elif name == "LinuxCNCServiceServicer":
        from .linuxcnc_service import LinuxCNCServiceServicer
        return LinuxCNCServiceServicer
    elif name == "HalServiceServicer":
        from .hal_service import HalServiceServicer
        return HalServiceServicer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Version
    "__version__",
    # Server components
    "create_server",
    "serve",
    "LinuxCNCServiceServicer",
    "HalServiceServicer",
    # Proto modules
    "linuxcnc_pb2",
    "linuxcnc_pb2_grpc",
    "hal_pb2",
    "hal_pb2_grpc",
]
