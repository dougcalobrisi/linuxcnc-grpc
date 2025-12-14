# linuxcnc-grpc

[![CI](https://github.com/dougcalobrisi/linuxcnc-grpc/actions/workflows/ci.yml/badge.svg)](https://github.com/dougcalobrisi/linuxcnc-grpc/actions/workflows/ci.yml)

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
linuxcnc-grpc-server --host 0.0.0.0 --port 50051
```

LinuxCNC must already be running before starting the server.

### Auto-start with LinuxCNC

To start the gRPC server automatically when LinuxCNC launches, add to your machine's HAL file:

```hal
# Start gRPC server (runs until LinuxCNC exits)
loadusr -W linuxcnc-grpc-server --host 0.0.0.0 --port 50051
```

Or use a dedicated HAL file via your INI:

```ini
[HAL]
POSTGUI_HALFILE = grpc-server.hal
```

The `-W` flag tells LinuxCNC to wait for the server to become ready before continuing.

## Quick Start

### Python

```bash
pip install linuxcnc-grpc
```

```python
import grpc
from linuxcnc_grpc._generated import linuxcnc_pb2, linuxcnc_pb2_grpc

channel = grpc.insecure_channel("localhost:50051")
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)

status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
print(f"Position: X={status.position.x:.3f} Y={status.position.y:.3f} Z={status.position.z:.3f}")
```

### Go

```bash
go get github.com/dougcalobrisi/linuxcnc-grpc
```

```go
import (
    pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"
)

conn, _ := grpc.NewClient("localhost:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
client := pb.NewLinuxCNCServiceClient(conn)

status, _ := client.GetStatus(context.Background(), &pb.GetStatusRequest{})
fmt.Printf("Position: X=%.3f Y=%.3f Z=%.3f\n", status.Position.X, status.Position.Y, status.Position.Z)
```

### Node.js / TypeScript

```bash
npm install linuxcnc-grpc
```

```typescript
import * as grpc from "@grpc/grpc-js";
import { LinuxCNCServiceClient, GetStatusRequest } from "linuxcnc-grpc";

const client = new LinuxCNCServiceClient("localhost:50051", grpc.credentials.createInsecure());

client.getStatus(GetStatusRequest.create(), (err, status) => {
  console.log(`Position: X=${status.position.x.toFixed(3)} Y=${status.position.y.toFixed(3)}`);
});
```

### Rust

```toml
[dependencies]
linuxcnc-grpc = "0.5"
tokio = { version = "1", features = ["full"] }
tonic = "0.12"
```

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::GetStatusRequest;

let mut client = LinuxCncServiceClient::connect("http://localhost:50051").await?;
let status = client.get_status(GetStatusRequest {}).await?.into_inner();
println!("Position: X={:.3} Y={:.3}", status.position.unwrap().x, status.position.unwrap().y);
```

## Examples

Complete examples for all supported languages:

| Example | Description | Python | Go | Node.js |
|---------|-------------|--------|-----|---------|
| `get_status` | Poll machine status | [view](examples/python/get_status.py) | [view](examples/go/cmd/get_status/main.go) | [view](examples/node/get_status.ts) |
| `stream_status` | Real-time status streaming | [view](examples/python/stream_status.py) | [view](examples/go/cmd/stream_status/main.go) | [view](examples/node/stream_status.ts) |
| `jog_axis` | Jog axes with keyboard | [view](examples/python/jog_axis.py) | [view](examples/go/cmd/jog_axis/main.go) | [view](examples/node/jog_axis.ts) |
| `mdi_command` | Execute G-code via MDI | [view](examples/python/mdi_command.py) | [view](examples/go/cmd/mdi_command/main.go) | [view](examples/node/mdi_command.ts) |
| `hal_query` | Query HAL pins/signals | [view](examples/python/hal_query.py) | [view](examples/go/cmd/hal_query/main.go) | [view](examples/node/hal_query.ts) |

See [examples/README.md](examples/README.md) for setup instructions.

## Services

- **LinuxCNCService** - Machine control: status, jogging, MDI, program execution
- **HalService** - HAL introspection: query pins, signals, parameters (read-only)

## Safety Warning

This server provides remote control of CNC machinery. Ensure proper safety measures:

- Use only on trusted networks
- Implement authentication in production (gRPC supports TLS/mTLS)
- Never leave machines unattended during remote operation
- Verify E-stop and safety systems are functional

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
make test          # Python tests
make test-all      # All languages

# Generate proto code
make proto-all     # Regenerate for all languages
```

See [CLAUDE.md](CLAUDE.md) for detailed development documentation.

## License

[GPL-2.0-or-later](https://www.gnu.org/licenses/gpl-2.0.html) (compatible with LinuxCNC)
