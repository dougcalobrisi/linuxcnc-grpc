<!-- AUTO-GENERATED from root README.md — do not edit directly -->
<!-- Regenerate with: ./scripts/generate-package-readmes.sh -->

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

## Installation

Add to `Cargo.toml`:

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
println!("Position: X={:.3} Y={:.3}", status.position.unwrap().x, status.position.unwrap().y);
```

## Examples

See the [examples directory](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/examples/rust) for complete Rust examples:

- `get_status` — Poll machine status
- `stream_status` — Real-time status streaming
- `jog_axis` — Jog axes with keyboard
- `mdi_command` — Execute G-code via MDI
- `hal_query` — Query HAL pins/signals
- `upload_file` — Upload, list, delete G-code files

## Services

- **LinuxCNCService** — Machine control: status, jogging, MDI, program execution, file management
- **HalService** — HAL introspection: query pins, signals, parameters (read-only)

## Documentation

See the [full documentation](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/docs) for server setup, API reference, and tutorials.

## Safety Warning

This package communicates with a gRPC server that controls real CNC machinery. Ensure proper safety measures:

- Use only on trusted networks
- Implement authentication in production (gRPC supports TLS/mTLS)
- Never leave machines unattended during remote operation
- Verify E-stop and safety systems are functional

## License

[MIT](https://github.com/dougcalobrisi/linuxcnc-grpc/blob/main/LICENSE)
