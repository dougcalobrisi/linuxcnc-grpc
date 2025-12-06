# linuxcnc-grpc-server

[![CI](https://github.com/dougcalobrisi/linuxcnc-grpc-server/actions/workflows/ci.yml/badge.svg)](https://github.com/dougcalobrisi/linuxcnc-grpc-server/actions/workflows/ci.yml)

gRPC server exposing LinuxCNC machine control and HAL (Hardware Abstraction Layer) functionality.

## Requirements

- Linux with LinuxCNC installed
- Python 3.8+
- Running LinuxCNC instance (for real hardware access)

## Installation

```bash
pip install linuxcnc-grpc-server
```

Or for development:

```bash
pip install -e .
```

## Quick Start

```bash
# Start the server (requires running LinuxCNC instance)
linuxcnc-grpc-server --host 0.0.0.0 --port 50051

# Or run as module
python -m linuxcnc_grpc_server.server --port 50051
```

## Services

- **LinuxCNCService** - Machine control via `linuxcnc.stat()` and `linuxcnc.command()`
- **HalService** - HAL introspection (read-only) via `hal.get_info_*()`

## Examples

The `examples/` directory contains Python client scripts demonstrating the gRPC API:

| Example | Description |
|---------|-------------|
| `get_status.py` | Connect and poll machine status |
| `jog_axis.py` | Jog an axis with keyboard control |
| `mdi_command.py` | Execute MDI commands (G-code) |
| `stream_status.py` | Stream real-time status updates |
| `hal_query.py` | Query HAL pins, signals, and parameters |

See [`examples/README.md`](examples/README.md) for setup instructions.

## Development

Install in development mode with test dependencies:

```bash
pip install -e ".[dev]"
```

Run the test suite (157 tests):

```bash
make test          # Run tests
make test-cov      # Run tests with coverage report
make lint          # Check Python syntax
```

Tests use mocked `linuxcnc` and `hal` modules, so no LinuxCNC installation or hardware is required to run them.

## Safety Warning

This server provides remote control of CNC machinery. Ensure proper safety measures:

- Use only on trusted networks
- Implement authentication in production
- Never leave machines unattended during remote operation
- Verify E-stop and safety systems are functional

## License

[GPL-2.0-or-later](https://www.gnu.org/licenses/gpl-2.0.html) (compatible with LinuxCNC licensing)
