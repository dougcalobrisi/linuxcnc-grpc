# LinuxCNC gRPC Server - Development Notes

## Project Setup

Repository: `dougcalobrisi/linuxcnc-grpc-server`

## Open Source Preparation (2025-12-03)

Added the following for open source release:

1. **`.gitignore`** - Standard Python gitignore (pycache, dist, eggs, venv, IDE files)

2. **`examples/`** - Python client examples demonstrating the gRPC API:
   - `get_status.py` - Basic status polling
   - `jog_axis.py` - Continuous and incremental jogging
   - `mdi_command.py` - MDI G-code execution with interactive mode
   - `stream_status.py` - Real-time status streaming
   - `hal_query.py` - HAL pin/signal/parameter querying
   - `README.md` - Setup instructions for examples

3. **`pyproject.toml`** - Fixed repository URL to `dougcalobrisi/linuxcnc-grpc-server`

4. **`tests/`** - Test suite (157 tests):
   - `conftest.py` - Pytest fixtures with mock HAL/LinuxCNC data
   - `test_examples_syntax.py` - Verifies example scripts compile
   - `test_hal_mapper.py` - Full unit tests for HalMapper (pure Python)
   - `test_linuxcnc_mapper.py` - Unit tests for LinuxCNCMapper (mocked linuxcnc)
   - `test_hal_service.py` - Full unit tests for HalServiceServicer (~45 tests)
   - `test_linuxcnc_service.py` - Full unit tests for LinuxCNCServiceServicer (~55 tests)

5. **`.github/workflows/ci.yml`** - GitHub Actions CI:
   - Python 3.8-3.12 matrix
   - Runs lint and tests on push/PR
   - Coverage reporting

6. **Updated files**:
   - `pyproject.toml` - Added pytest config, pytest-cov deps
   - `Makefile` - Added `make test` and `make test-cov` targets
   - `README.md` - Added CI badge

## Updates (2025-12-04)

- **Examples import fix**: Updated all examples to import from installed package first (`linuxcnc_grpc_server._generated`), with fallback to local `pb/` directory. This allows examples to work out-of-box when package is installed via `pip install -e .`

- **License change**: Changed from `GPL-2.0-only` to `GPL-2.0-or-later`. This maintains compatibility with LinuxCNC (GPLv2) while allowing future compatibility if LinuxCNC ever upgrades to GPLv3.

## Updates (2025-12-05)

- **Added full service layer tests**: Increased test count from 68 to 157 tests by adding comprehensive unit tests for:
  - `HalServiceServicer` (~45 tests) - Tests for GetSystemStatus, SendCommand, GetValue, QueryPins, QuerySignals, QueryParams, QueryComponents, StreamStatus, WatchValues RPCs
  - `LinuxCNCServiceServicer` (~55 tests) - Tests for GetStatus, SendCommand (all command types: state, mode, mdi, jog, home, spindle, feedrate, coolant, program, etc.), WaitComplete, StreamStatus, StreamErrors RPCs

- **Service layer test coverage**: The gRPC service layer now has comprehensive test coverage using mocked `hal` and `linuxcnc` modules.

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
npm install ts-proto
# or globally: npm install -g ts-proto
```

Generated code locations:
- Python: `src/linuxcnc_grpc_server/_generated/` (committed)
- Go: `gen/go/` (gitignored)
- Rust: `gen/rust/` (gitignored)
- Node.js/TypeScript: `gen/node/` (gitignored)

## Still Missing (consider adding)

- `CONTRIBUTING.md`
- `SECURITY.md` (recommended for machine control software)
- `CODE_OF_CONDUCT.md`
