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
│   ├── _generated/            # Re-exports from linuxcnc_pb (backwards compatibility)
│   └── *.py                   # Server implementation
├── packages/
│   ├── python/linuxcnc_pb/    # Python generated protobuf code
│   ├── node/                  # npm package (linuxcnc-grpc)
│   │   ├── package.json
│   │   └── src/*.ts           # Generated TypeScript
│   └── rust/                  # Rust crate (linuxcnc-grpc)
│       ├── Cargo.toml
│       ├── proto/             # Proto files (copied for crate packaging)
│       └── src/               # Rust source (uses tonic for codegen)
├── scripts/                   # Build, publish, and utility scripts
│   ├── generate-protos.sh     # Generate proto code for all languages
│   ├── build-*.sh             # Build scripts per language
│   ├── publish-*.sh           # Publish scripts per registry
│   └── sync-versions.sh       # Version management
├── docs/                      # Documentation
│   ├── README.md              # Documentation index
│   ├── getting-started.md     # Installation and quickstart
│   ├── server.md              # Server configuration
│   ├── api-reference.md       # Complete API documentation
│   └── examples.md            # Examples guide
└── examples/                  # Multi-language client examples
    ├── python/                # Python examples
    ├── go/                    # Go examples (in cmd/ subdirectories)
    ├── node/                  # Node.js/TypeScript examples
    └── rust/                  # Rust examples (cargo binaries)
```

**`examples/`** - Client examples in all supported languages:

Each language directory contains equivalent implementations:
- `get_status` - Basic status polling
- `stream_status` - Real-time status streaming
- `jog_axis` - Continuous and incremental jogging
- `mdi_command` - MDI G-code execution with interactive mode
- `hal_query` - HAL pin/signal/parameter querying
- `upload_file` - Upload, list, and delete G-code files

Run examples:
```bash
# Python
cd examples/python && python get_status.py

# Go
cd examples/go && go run ./cmd/get_status

# Node.js/TypeScript
cd examples/node && npm install && npx tsx get_status.ts

# Rust
cd examples/rust && cargo run --bin get_status
```

## Package Names

All packages use consistent naming across registries:

| Registry  | Package Name                                |
|-----------|---------------------------------------------|
| PyPI      | `linuxcnc-grpc`                             |
| npm       | `linuxcnc-grpc`                             |
| crates.io | `linuxcnc-grpc`                             |
| Go        | `github.com/dougcalobrisi/linuxcnc-grpc`    |

## Server Architecture Notes

**Thread Safety**: `LinuxCNCServiceServicer` uses a `threading.RLock` to protect all
accesses to `self._stat`, `self._command`, and `self._error_channel`. Streaming RPCs
(`StreamStatus`, `StreamErrors`) hold the lock only during poll/map operations, releasing
it before `sleep` and `yield` to avoid blocking other threads.

**Command Dispatch**: `SendCommand` uses an explicit `_command_handlers` dict mapping
command type strings to handler methods (no dynamic `getattr` dispatch).

**Input Validation**: Handler methods validate indices before dispatching to LinuxCNC:
- `_validate_joint_index(index, is_joint)` checks bounds against `stat.joint`/`stat.axis`; index -1 is only valid for joint operations (home/unhome all).
- `_validate_spindle_index(index)` checks bounds against `stat.spindle`.
- `_validate_nc_path(filename)` resolves paths relative to the NC files directory and rejects path traversal, null bytes, and empty filenames. Used by both `_handle_program_cmd` and the file management RPCs.
- MDI commands reject empty strings and null bytes, and log the full command at WARNING level for audit.

**File Management RPCs**: `UploadFile`, `ListFiles`, and `DeleteFile` provide remote file
management for the NC files directory (`/home/linuxcnc/linuxcnc/nc_files` or `LINUXCNC_NC_FILES` env var).
These RPCs do not require the LinuxCNC lock (no stat/command access) and use `_validate_nc_path`
for security. Upload max size is 10 MB.

## Development Setup

Requires [uv](https://docs.astral.sh/uv/) for Python dependency management:

```bash
make setup       # Install all dev + build deps (creates .venv)
make test        # Run tests
make test-cov    # Run tests with coverage report
make lint        # Check Python syntax
```

All Python commands use `uv run` to execute within the managed `.venv`.
On a LinuxCNC machine, use `make install` which creates the venv with
`--system-site-packages` so the `linuxcnc` Python module is accessible.

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

- **Python**: `packages/python/linuxcnc_pb/` (re-exported via `src/linuxcnc_grpc/_generated/` for backwards compatibility)
- **Go**: `*.pb.go` at repo root
- **Rust**: Generated at build time via `build.rs` (proto files in `packages/rust/proto/`)
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
- **release.yml**: Unified release workflow (manual dispatch) - publishes to PyPI, npm, and crates.io

### CI Job Structure

```
proto-check ─────────────────────────────────────────────────────┐
lint ──► test-python ──► test-node ──► examples-node            │
                     ├──► test-rust ──► examples-rust           │
                     └──► test-go ───► examples-go              │
                     └──► examples-python                       │
```

### CI Caching Strategy

The CI workflow uses aggressive caching to minimize build times:

| Cache | Key | Jobs |
|-------|-----|------|
| protoc binary | `protoc-{VERSION}-linux` | proto-check, test-rust, examples-rust |
| pip packages | `pyproject.toml` hash | All jobs with Python |
| npm packages | `package-lock.json` hash | test-node, examples-node |
| Go modules | Built-in (setup-go@v5) | test-go, examples-go |
| Cargo (Rust) | `Swatinem/rust-cache` with `shared-key: rust-ci` | test-rust, examples-rust |
| Cargo bin plugins | `cargo-bin-proto-plugins-{OS}-v1` | proto-check |

### Releasing

1. Update versions: `make sync-version VERSION=x.y.z`
2. Commit and push to main
3. Go to GitHub Actions > Release > Run workflow
   - `dry_run`: Test without publishing
   - `create_tag`: Create git tag and GitHub Release after publish

The release workflow:
- Validates version consistency across all packages
- Runs full CI suite before publishing
- Publishes Python, Node.js, and Rust in parallel
- Creates git tag and GitHub Release after all succeed

Required secrets/environments:
- `pypi` environment: Uses trusted publishing (OIDC)
- `npm` environment: `NPM_TOKEN` secret
- `crates` environment: `CARGO_REGISTRY_TOKEN` secret

## Documentation

User-facing documentation is in the `docs/` directory:

| Document | Description |
|----------|-------------|
| [docs/README.md](docs/README.md) | Documentation index and overview |
| [docs/getting-started.md](docs/getting-started.md) | Installation and quickstart |
| [docs/server.md](docs/server.md) | Server configuration and setup |
| [docs/api-reference.md](docs/api-reference.md) | Complete API documentation |
| [docs/examples.md](docs/examples.md) | Examples guide and walkthroughs |
