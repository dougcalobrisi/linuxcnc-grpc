# linuxcnc-grpc

[![PyPI](https://img.shields.io/pypi/v/linuxcnc-grpc)](https://pypi.org/project/linuxcnc-grpc/)
[![npm](https://img.shields.io/npm/v/linuxcnc-grpc)](https://www.npmjs.com/package/linuxcnc-grpc)
[![crates.io](https://img.shields.io/crates/v/linuxcnc-grpc)](https://crates.io/crates/linuxcnc-grpc)
[![Go Reference](https://pkg.go.dev/badge/github.com/dougcalobrisi/linuxcnc-grpc.svg)](https://pkg.go.dev/github.com/dougcalobrisi/linuxcnc-grpc)

gRPC interface for LinuxCNC machine control and HAL (Hardware Abstraction Layer).

## Why gRPC?

LinuxCNC's native Python API only works locally. This project exposes it over gRPC, enabling:

- **Remote monitoring** - Build web dashboards, mobile apps, or desktop GUIs
- **Multi-machine management** - Monitor a fleet of CNC machines from one place
- **Any-language integration** - Use Go, Node.js, Rust, or any gRPC-supported language
- **Real-time streaming** - Subscribe to status updates instead of polling

## Running the Server

The server runs on your LinuxCNC machine and exposes the gRPC interface.

### Basic Usage

```bash
pip install linuxcnc-grpc
# or with uv
uv pip install linuxcnc-grpc
```

```bash
linuxcnc-grpc --host 0.0.0.0 --port 50051
```

> **Security warning:** the server has no authentication and no TLS. Anyone
> who can reach the port can command the machine. Bind to `127.0.0.1` and
> connect through an SSH tunnel (`ssh -N -L 50051:127.0.0.1:50051 user@cnc-machine`),
> or use `--host 0.0.0.0` only on a trusted LAN. See
> [Security Considerations](https://doug.sh/docs/linuxcnc-grpc/server/#security-considerations).

LinuxCNC must already be running before starting the server.

### Auto-start with LinuxCNC

To start the gRPC server automatically when LinuxCNC launches, add to your machine's HAL file:

```hal
# Start gRPC server (runs until LinuxCNC exits)
loadusr linuxcnc-grpc --host 0.0.0.0 --port 50051
```

Or use a dedicated HAL file via your INI:

```ini
[HAL]
POSTGUI_HALFILE = grpc-server.hal
```

## Quick Start

### Python

```bash
pip install linuxcnc-grpc
```

> [PyPI package](https://pypi.org/project/linuxcnc-grpc/)

```python
import grpc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

channel = grpc.insecure_channel("localhost:50051")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
pos = status.position.actual_position
print(f"Position: X={pos.x:.3f} Y={pos.y:.3f} Z={pos.z:.3f}")
```

### Go

```bash
go get github.com/dougcalobrisi/linuxcnc-grpc
```

> [pkg.go.dev](https://pkg.go.dev/github.com/dougcalobrisi/linuxcnc-grpc)

```go
import (
    pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"
)

conn, _ := grpc.NewClient("localhost:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
client := pb.NewLinuxCNCServiceClient(conn)

status, _ := client.GetStatus(context.Background(), &pb.GetStatusRequest{})
pos := status.GetPosition().GetActualPosition()
fmt.Printf("Position: X=%.3f Y=%.3f Z=%.3f\n", pos.GetX(), pos.GetY(), pos.GetZ())
```

### Node.js / TypeScript

```bash
npm install linuxcnc-grpc
```

> [npm package](https://www.npmjs.com/package/linuxcnc-grpc)

```typescript
import * as grpc from "@grpc/grpc-js";
import { LinuxCNCServiceClient, GetStatusRequest } from "linuxcnc-grpc";

const client = new LinuxCNCServiceClient("localhost:50051", grpc.credentials.createInsecure());

client.getStatus(GetStatusRequest.create(), (err, status) => {
  if (err) throw err;
  const pos = status!.position!.actualPosition!;
  console.log(`Position: X=${pos.x.toFixed(3)} Y=${pos.y.toFixed(3)} Z=${pos.z.toFixed(3)}`);
});
```

### Rust

> [crates.io](https://crates.io/crates/linuxcnc-grpc)

```toml
[dependencies]
linuxcnc-grpc = "1.0"
tokio = { version = "1", features = ["full"] }
tonic = "0.12"
```

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::GetStatusRequest;

let mut client = LinuxCncServiceClient::connect("http://localhost:50051").await?;
let status = client.get_status(GetStatusRequest {}).await?.into_inner();
if let Some(pos) = status.position.as_ref().and_then(|p| p.actual_position.as_ref()) {
    println!("Position: X={:.3} Y={:.3} Z={:.3}", pos.x, pos.y, pos.z);
}
```

## Using with Claude (MCP)

[linuxcnc-mcp](https://github.com/dougcalobrisi/linuxcnc-mcp) is an MCP server and Claude Code plugin built on this API. It is read-only by default; control tools are opt-in and, by default, ask for approval before each call. See [Using with Claude](https://doug.sh/docs/linuxcnc-grpc/using-with-claude/).

## Examples

Complete examples for all supported languages:

| Example | Description | Python | Go | Node.js | Rust |
|---------|-------------|--------|-----|---------|------|
| `get_status` | Poll machine status | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/python/get_status.py) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/go/cmd/get_status/main.go) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/node/get_status.ts) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust/src/bin/get_status.rs) |
| `stream_status` | Real-time status streaming | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/python/stream_status.py) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/go/cmd/stream_status/main.go) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/node/stream_status.ts) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust/src/bin/stream_status.rs) |
| `jog_axis` | Jog axes with keyboard | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/python/jog_axis.py) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/go/cmd/jog_axis/main.go) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/node/jog_axis.ts) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust/src/bin/jog_axis.rs) |
| `mdi_command` | Execute G-code via MDI | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/python/mdi_command.py) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/go/cmd/mdi_command/main.go) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/node/mdi_command.ts) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust/src/bin/mdi_command.rs) |
| `hal_query` | Query HAL pins/signals | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/python/hal_query.py) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/go/cmd/hal_query/main.go) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/node/hal_query.ts) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust/src/bin/hal_query.rs) |
| `upload_file` | Upload, list, delete G-code files | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/python/upload_file.py) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/go/cmd/upload_file/main.go) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/node/upload_file.ts) | [view](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust/src/bin/upload_file.rs) |

See [examples/README.md](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/README.md) for setup instructions.

## Services

- **LinuxCNCService** - Machine control: status, jogging, MDI, program execution, file management
- **HalService** - HAL introspection: query pins, signals, parameters (read-only)

### File Management

The server provides `UploadFile`, `ListFiles`, `ReadFile`, `DeleteFile` and `ResolveFile` RPCs for remote G-code file management. Files are stored in the NC files directory (default: `/home/linuxcnc/linuxcnc/nc_files`).

Configure the directory with `--nc-files` or the `LINUXCNC_NC_FILES` environment variable:

```bash
linuxcnc-grpc --host 0.0.0.0 --nc-files /path/to/nc_files
```

## Safety Warning

This server provides remote control of CNC machinery, and it has **no
authentication and no TLS**: anyone who can reach the port can command the
machine. Ensure proper safety measures:

- Bind to localhost (`--host 127.0.0.1`) and connect through an SSH tunnel:
  `ssh -N -L 50051:127.0.0.1:50051 user@cnc-machine`
- Otherwise use `--host 0.0.0.0` only on a trusted, isolated LAN, never on a
  shared network or the internet
- Never leave machines unattended during remote operation
- Verify E-stop and safety systems are functional

## Production Deployment

The packaged server does not enable TLS; the SSH tunnel above is the supported
way to secure it. If you embed the servicers in your own server, you can add
TLS yourself:

```python
# Server with TLS
credentials = grpc.ssl_server_credentials([(private_key, certificate)])
server.add_secure_port('[::]:50051', credentials)
```

```python
# Client with TLS
credentials = grpc.ssl_channel_credentials(root_certificates)
channel = grpc.secure_channel('your-machine:50051', credentials)
```

See [Server Configuration](https://doug.sh/docs/linuxcnc-grpc/server/#security-considerations) for complete TLS setup instructions.

## Development

Requires [uv](https://docs.astral.sh/uv/) for Python dependency management:

```bash
# Install dev dependencies
make setup

# Run tests
make test          # Python tests
make test-all      # All languages

# Generate proto code
make proto-all     # Regenerate for all languages
```

See [CLAUDE.md](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/CLAUDE.md) for detailed development documentation.

## License

[MIT](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/LICENSE)
