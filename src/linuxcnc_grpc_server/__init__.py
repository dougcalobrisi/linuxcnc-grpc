"""LinuxCNC gRPC Server - Remote machine control via gRPC."""

__version__ = "0.1.0"

# Lazy imports - server components require linuxcnc module which is only
# available on LinuxCNC machines. Client code can import ._generated directly.


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
    "__version__",
    "create_server",
    "serve",
    "LinuxCNCServiceServicer",
    "HalServiceServicer",
]
