# LinuxCNC gRPC - Development Notes

## Project Setup

Repository: `dougcalobrisi/linuxcnc-grpc`

## Project Structure

```
/                              # Go module at repo root
├── go.mod                     # module github.com/dougcalobrisi/linuxcnc-grpc
├── *.pb.go                    # Generated Go code
├── proto/                     # Proto source files (canonical location)
│   ├── linuxcnc.proto
│   └── hal.proto
├── src/linuxcnc_grpc/         # Python package (PyPI: linuxcnc-grpc)
│   ├── _generated/            # Python generated code
│   └── *.py                   # Server implementation
├── packages/
│   ├── node/                  # npm package (linuxcnc-grpc)
│   │   ├── package.json
│   │   └── src/*.ts           # Generated TypeScript
│   └── rust/                  # Rust crate (linuxcnc-grpc)
│       ├── Cargo.toml
│       └── src/               # Generated Rust code
├── scripts/                   # Build, publish, and utility scripts
│   ├── generate-protos.sh     # Generate proto code for all languages
│   ├── build-*.sh             # Build scripts per language
│   ├── publish-*.sh           # Publish scripts per registry
│   └── sync-versions.sh       # Version management
└── examples/                  # Multi-language client examples
    ├── python/                # Python examples
    ├── go/                    # Go examples (in cmd/ subdirectories)
    └── node/                  # Node.js/TypeScript examples
```

**`examples/`** - Client examples in all supported languages:

Each language directory contains equivalent implementations:
- `get_status` - Basic status polling
- `stream_status` - Real-time status streaming
- `jog_axis` - Continuous and incremental jogging
- `mdi_command` - MDI G-code execution with interactive mode
- `hal_query` - HAL pin/signal/parameter querying

Run examples:
```bash
# Python
cd examples/python && python get_status.py

# Go
cd examples/go && go run ./cmd/get_status

# Node.js/TypeScript
cd examples/node && npm install && npx tsx get_status.ts
```

## Package Names

All packages use consistent naming across registries:

| Registry  | Package Name                                |
|-----------|---------------------------------------------|
| PyPI      | `linuxcnc-grpc`                             |
| npm       | `linuxcnc-grpc`                             |
| crates.io | `linuxcnc-grpc`                             |
| Go        | `github.com/dougcalobrisi/linuxcnc-grpc`    |

## Running Tests

```bash
make test        # Run tests
make test-cov    # Run tests with coverage report
make lint        # Check Python syntax
```

## Generating Protos

```bash
make proto         # Python only (default)
make proto-go      # Python + Go
make proto-rust    # Python + Rust
make proto-node    # Python + Node.js/TypeScript
make proto-all     # All languages
```

**Go prerequisites:**
```bash
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
```

**Rust prerequisites:**
```bash
cargo install protoc-gen-prost protoc-gen-tonic
```

**Node.js/TypeScript prerequisites:**
```bash
cd packages/node && npm install
# or globally: npm install -g ts-proto
```

## Generated Code Locations

All generated code is **committed** so users don't need protoc:

- **Python**: `src/linuxcnc_grpc/_generated/`
- **Go**: `*.pb.go` at repo root
- **Rust**: `packages/rust/src/linuxcnc/`, `packages/rust/src/hal/`
- **Node.js**: `packages/node/src/linuxcnc.ts`, `packages/node/src/hal.ts`

## Client Installation

### Python
```bash
pip install linuxcnc-grpc
```

### Go
```bash
go get github.com/dougcalobrisi/linuxcnc-grpc
```

```go
import linuxcnc "github.com/dougcalobrisi/linuxcnc-grpc"
```

### Node.js/TypeScript
```bash
npm install linuxcnc-grpc
```

```typescript
import { LinuxCNCServiceClient, GetStatusRequest } from 'linuxcnc-grpc';
```

### Rust
```toml
[dependencies]
linuxcnc-grpc = "0.5"
```

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
```

## Scripts

All automation scripts are in the `scripts/` directory:

| Script | Purpose |
|--------|---------|
| `generate-protos.sh` | Generate protobuf code for all languages |
| `build-python.sh` | Build Python wheel and sdist |
| `build-node.sh` | Build Node.js/TypeScript package |
| `build-rust.sh` | Build Rust crate |
| `build-all.sh` | Build all packages |
| `publish-python.sh` | Publish to PyPI |
| `publish-node.sh` | Publish to npm |
| `publish-rust.sh` | Publish to crates.io |
| `publish-all.sh` | Publish all packages (with confirmation) |
| `sync-versions.sh` | Synchronize version across all packages |

### Building Packages

```bash
make build-all     # Build all packages
make build-python  # Build Python only
make build-node    # Build Node.js only
make build-rust    # Build Rust only
```

### Publishing Packages

```bash
make publish-all      # Publish all (with confirmation)
make publish-dry-run  # Test publish without uploading
```

### Version Management

```bash
make sync-version VERSION=0.6.0  # Update all package versions
./scripts/sync-versions.sh 0.6.0 --commit --tag  # With git commit and tag
```

## CI/CD

GitHub Actions workflows:

- **ci.yml**: Runs on push/PR - tests all languages, checks proto freshness
- **release-python.yml**: Publishes to PyPI on `v*` tag
- **release-node.yml**: Publishes to npm on `v*` tag
- **release-rust.yml**: Publishes to crates.io on `v*` tag

Required secrets for publishing:
- `NPM_TOKEN`: npm authentication token
- `CARGO_REGISTRY_TOKEN`: crates.io API token
- PyPI: Uses trusted publishing (OIDC)
