# LinuxCNC gRPC - Development Notes

## Project Setup

Repository: `dougcalobrisi/linuxcnc-grpc`

## Project Structure

```
/                              # Go module at repo root
├── go.mod                     # module github.com/dougcalobrisi/linuxcnc-grpc
├── CHANGELOG.md               # Release notes (see Changelog below)
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
├── client.go                  # Go gRPC client wrapper
├── client_test.go             # Go client tests
├── scripts/                   # Build, publish, and utility scripts
│   ├── generate-protos.sh     # Generate proto code for all languages
│   ├── build-*.sh             # Build scripts per language
│   ├── publish-*.sh           # Publish scripts per registry
│   ├── sync-versions.sh       # Version management
│   └── wait-for-linuxcnc.py   # Poll LinuxCNC readiness (e2e CI)
├── tests/                     # Python test suite
│   ├── test_e2e.py            # E2E tests against real LinuxCNC simulator
│   ├── test_integration.py    # Integration tests with mock server
│   ├── test_*_mapper.py       # Unit tests for proto mappers
│   ├── test_*_service.py      # Unit tests for gRPC services
│   ├── test_wait_for_linuxcnc.py  # scripts/wait-for-linuxcnc.py
│   ├── mock_server.py         # Mock gRPC server for integration tests
│   └── conftest.py            # Shared fixtures
├── docs/                      # Hugo documentation site
│   ├── hugo.toml              # Hugo configuration
│   ├── README.md              # Docs development guide
│   ├── content/               # Markdown documentation pages
│   │   ├── _index.md          # Documentation index
│   │   ├── getting-started.md # Installation and quickstart
│   │   ├── server.md          # Server configuration
│   │   ├── api-reference.md   # Complete API documentation
│   │   ├── examples.md        # Examples guide
│   │   ├── tutorial.md        # Step-by-step tutorial
│   │   └── e2e-testing.md     # E2E testing guide
│   └── static/                # Static assets
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

## Package READMEs

`packages/node/README.md` and `packages/rust/README.md` are **auto-generated** from the root `README.md`
by `scripts/generate-package-readmes.sh` (or `make readme`). These are what npm and crates.io display on
their package pages. CI checks they are up-to-date via the `readme-check` job.

The root `README.md` uses absolute GitHub URLs for all links so it renders correctly on PyPI and pkg.go.dev.

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
linuxcnc-grpc = "1.0"
```

```rust
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
```

## Scripts

All automation scripts are in the `scripts/` directory:

| Script | Purpose |
|--------|---------|
| `generate-protos.sh` | Generate protobuf code for all languages |
| `generate-package-readmes.sh` | Generate package READMEs for npm/crates.io from root README |
| `build-python.sh` | Build Python wheel and sdist |
| `build-node.sh` | Build Node.js/TypeScript package |
| `build-rust.sh` | Build Rust crate |
| `build-all.sh` | Build all packages |
| `publish-python.sh` | Publish to PyPI |
| `publish-node.sh` | Publish to npm |
| `publish-rust.sh` | Publish to crates.io |
| `publish-all.sh` | Publish all packages (with confirmation). Refuses a working tree with uncommitted changes unless given `--allow-dirty`; with `--tag`, checks an existing local or remote tag first and creates the tag only if every package was published |
| `sync-versions.sh` | Synchronize version across all packages, the example projects and doc examples |
| `common.sh` | Shared helper functions sourced by other scripts |
| `wait-for-linuxcnc.py` | Poll LinuxCNC readiness (used by e2e CI). Each attempt, and the initial `import linuxcnc` check, runs in a child process with a timeout; `--timeout` covers the whole run |

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
make sync-version VERSION=0.6.0           # Stable release
make sync-version VERSION=0.6.0-beta.1    # Pre-release (also accepts 0.6.0b1)
./scripts/sync-versions.sh 0.6.0 --commit --tag  # With git commit and tag
```

Pre-release versions are automatically converted per ecosystem:
- **Python (PEP 440)**: `0.6.0b1`, `0.6.0a1`, `0.6.0rc1`
- **npm/Rust (semver)**: `0.6.0-beta.1`, `0.6.0-alpha.1`, `0.6.0-rc.1`

Either format can be passed as input; the script converts to the correct format for each package.

The script also auto-discovers and updates Rust dependency version strings (major.minor) in all `.md` files containing `linuxcnc-grpc = "X.Y"`.

It also sets the versions of the example projects (`examples/rust/Cargo.toml`,
`examples/node/package.json`) and re-locks `examples/node/package-lock.json` and the Cargo
lock files.

Lock files: in `uv.lock` the script edits only the project's own version entry (no re-resolve,
so no dependency moves) and then runs `uv lock --check`. A lock file that cannot be updated is
reported, the script exits non-zero, and no commit or tag is made. Do not put `uv lock
--upgrade` back: a newer `uv` re-resolves every dependency, and a new `grpcio-tools` changes
the generated Python code and fails CI's proto check.

`--tag` requires `--commit`: a tag made without the commit would point at the commit before
the bump.

## CI/CD

GitHub Actions workflows:

- **ci.yml**: Runs on push/PR - tests all languages, checks proto freshness, checks package README freshness
- **e2e.yml**: Runs on push/PR - e2e tests against a real LinuxCNC simulator (builds from source on Ubuntu 24.04). The tests skip themselves when the server cannot be reached and pytest counts that as success, so a step after the run reads the JUnit report and fails the job if no test passed or any test was skipped for a reason not listed in `TOLERATED_SKIP_REASONS` The LinuxCNC build cache key includes `LINUXCNC_COMMIT` as well as the tag and platform, and the "Verify LinuxCNC commit" step compares the checkout's `git rev-parse HEAD` with it on every run, restored or freshly cloned; change `LINUXCNC_VERSION` and `LINUXCNC_COMMIT` together.
- **release.yml**: Unified release workflow (manual dispatch) - publishes to PyPI, npm, and crates.io
- **version-bump.yml**: Creates a version bump PR (manual dispatch) - updates all package versions via `sync-versions.sh`

Actions in all workflows are pinned to commit SHAs, with the version in a trailing comment
(`uses: owner/action@<sha> # vX.Y.Z`). Update the SHA and the comment together.

### CI Job Structure

```
proto-check ─────────────────────────────────────────────────────┐
readme-check                                                     │
lint ──► test-python ──► test-node ──► examples-node            │
                     ├──► test-rust ──► examples-rust           │
                     └──► test-go ───► examples-go              │
                     └──► examples-python                       │
```

### CI Caching Strategy

The CI workflow uses aggressive caching to minimize build times:

| Cache | Key | Jobs |
|-------|-----|------|
| protoc binary | `protoc-{VERSION}-linux-x86_64-sha256-{PROTOC_SHA256}` | proto-check, test-rust, examples-rust |
| pip packages | `pyproject.toml` hash | All jobs with Python |
| npm packages | `package-lock.json` hash | test-node, examples-node |
| Go modules | Built-in (setup-go@v5) | test-go, examples-go |
| Cargo (Rust) | `Swatinem/rust-cache` with `shared-key: rust-ci` | test-rust, examples-rust |
| Cargo bin plugins | `cargo-bin-proto-plugins-{OS}-prost-{VERSION}-tonic-{VERSION}-v2` | proto-check |

The protoc download is checked against `PROTOC_SHA256`, which is also part of the cache key.
`PROTOC_SHA256` must change together with `PROTOC_VERSION`, in both `ci.yml` and `release.yml`.

The stream examples run under `timeout 5`; exit status 124 (stopped by the timeout) is the
expected result and any other non-zero status fails the job.

### Changelog

`CHANGELOG.md` at the repo root is the release notes. Every user-visible change adds an entry
under `## Unreleased` in the same commit as the change (sections: Added, Changed, Upgrade
notes, Deprecated, Fixed, Documentation, and where needed Known issues and Maintainer
tooling). The version bump commit renames `## Unreleased` to
the version. The protos carry no version of their own (packages are plain `linuxcnc` and
`hal`, and renaming them would change every RPC path): the package version is the API version.
Additive proto changes are a minor bump; anything wire-incompatible is a major one.

### Releasing

1. Update versions: `make sync-version VERSION=x.y.z`
   - If the script reports a lock file it could not update, fix that first (see Version
     Management); nothing has been committed at that point.
   - Rename `## Unreleased` in `CHANGELOG.md` to the version.
2. Commit and push to main
3. Go to GitHub Actions > Release > Run workflow
   - `dry_run`: Test without publishing
   - `create_tag`: Create git tag and GitHub Release after publish
   - `resume_partial_release`: leave false. Set it to true only when re-running a release
     that failed part-way, on the same commit (see below)

The GitHub Release body comes from `CHANGELOG.md`: the `create-release` job copies the section
whose heading is exactly `## <version>` (up to the next `## ` heading) and appends the package
links. If there is no such section, the body is the package links plus GitHub's generated
notes. So the heading must be renamed from `## Unreleased` before the release is run.

The release workflow:
- Validates version consistency across all packages (normalizes PEP 440 vs semver)
- Runs full CI suite before publishing
- Verifies build artifacts match expected versions before uploading
- Publishes Python, Node.js, and Rust in parallel
- Auto-detects npm `--tag` for pre-release versions (beta, alpha, rc)
- Publishes npm packages with `--provenance` for supply chain attestation
- Marks GitHub Releases as pre-release when version contains a pre-release suffix
- Creates git tag and GitHub Release after all succeed
- Runs one release at a time (`concurrency: release`; a second run waits)
- Asks the remote for the tag before publishing anything: a tag that points at another commit
  stops the run, one that points at this commit is accepted
- Fails if a registry already has the version, unless the run was started with the input
  `resume_partial_release: true` (default false). Only then is an already-published version
  skipped (PyPI `skip-existing`, an `npm view` check, the crates.io index). Use it only to
  re-run a failed release on the same commit as the failed run: on any other commit the skip
  would let the release be tagged at a commit the published packages were not built from

Authentication (all use OIDC trusted publishing):
- `pypi` environment: OIDC via `pypa/gh-action-pypi-publish`
- `npm` environment: OIDC trusted publishing (configured on npmjs.com, requires npm >= 11.5.1)
- `crates` environment: OIDC via `rust-lang/crates-io-auth-action` (configured on crates.io)

## Documentation

User-facing documentation is a Hugo static site in the `docs/` directory, deployed to GitHub Pages.

**Published site:** https://doug.sh/docs/linuxcnc-grpc/

| Document | Description |
|----------|-------------|
| [docs/content/_index.md](docs/content/_index.md) | Documentation index and overview |
| [docs/content/getting-started.md](docs/content/getting-started.md) | Installation and quickstart |
| [docs/content/server.md](docs/content/server.md) | Server configuration and setup |
| [docs/content/api-reference.md](docs/content/api-reference.md) | Complete API documentation |
| [docs/content/examples.md](docs/content/examples.md) | Examples guide and walkthroughs |
| [docs/content/tutorial.md](docs/content/tutorial.md) | Step-by-step tutorial |
| [docs/content/e2e-testing.md](docs/content/e2e-testing.md) | E2E testing with LinuxCNC simulator |

### Local docs development

```bash
make docs-serve    # Downloads theme + starts live-reload server at localhost:1313
make docs-build    # Production build to docs/public/
```

The Hugo theme (hugo-book) is downloaded at build time and gitignored. The GitHub Actions workflow (`.github/workflows/docs.yml`) builds and deploys on push to main when `docs/**` changes.
