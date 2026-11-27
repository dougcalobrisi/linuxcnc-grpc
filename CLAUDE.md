# LinuxCNC gRPC - Development Notes

## Project Setup

Repository: `dougcalobrisi/linuxcnc-grpc`

## Project Structure

```
/                              # Go module at repo root
├── go.mod                     # module github.com/dougcalobrisi/linuxcnc-grpc
├── CHANGELOG.md               # Release notes (see Changelog below)
├── proto/                     # Proto source files (canonical location)
│   ├── linuxcnc.proto
│   └── hal.proto
├── src/linuxcnc_grpc/         # Python package (PyPI: linuxcnc-grpc)
│   ├── _generated/            # Re-exports from linuxcnc_pb (backwards compatibility)
│   └── *.py                   # Server implementation
├── packages/
│   ├── go/                    # Generated Go code (*.pb.go, package pb)
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
│   │   ├── server-setup.md    # Install and start the server
│   │   ├── server.md          # Server configuration
│   │   ├── api-reference.md   # Complete API documentation
│   │   ├── examples.md        # Examples guide
│   │   ├── tutorial.md        # Step-by-step tutorial
│   │   ├── e2e-testing.md     # E2E testing guide
│   │   └── using-with-claude.md  # The linuxcnc-mcp server built on this API
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

**Deprecated `AutoCommand`**: the `AutoCommand` message and `AutoCommandType` enum carry
`option deprecated = true`. They are not in the `LinuxCNCCommand` oneof and never were;
program control goes through `ProgramCommand`. Keep them until 2.0 (removing a message is
a breaking change) and do not wire them in.

**Command Response Semantics**: `SendCommand` returns `RCS_DONE` as soon as the handler
has dispatched the command; that means "dispatched", not "finished". `CommandResponse.serial`
is `request.serial` or, when that is 0, the servicer's own counter (`_next_serial`). It is
never LinuxCNC's serial and must not be compared with `echo_serial_number`. Changing either
of these is a breaking change. The counter wraps to 1 after `MAX_RESPONSE_SERIAL`
(2147483647, the field is an int32). `HalServiceServicer._next_serial` does the same with its
own `MAX_RESPONSE_SERIAL` (defined in `hal_service.py`, because importing
`linuxcnc_service` needs the `linuxcnc` module) and its own small lock.

**Command Identification (`linuxcnc_serial`)**: `CommandResponse.linuxcnc_serial`
(`optional int32`, field 4) carries LinuxCNC's real serial for the command, the value
`_record_dispatch` returns. It is set only on a successful dispatch with bindings that expose
`command.serial`; rejected commands and the fallback path leave it unset (check with
`HasField`). It identifies the command LinuxCNC was given; it does not prove the task received
it. `command.serial` is read after the handler returns and the echo is not checked. LinuxCNC's
send normally blocks until the task echoes the serial, but if the task does not answer it gives
up after about 5 seconds without raising, and the serial is still returned. Receipt and outcome
are confirmed with WaitComplete or the status. Multi-message handlers (coolant, set_limit,
override_config, program_options) report the serial of the last message LinuxCNC took.

**A command LinuxCNC did not take**: when the write to the command channel fails,
`emcSendCommand` returns before it updates the channel's serial, and the Python wrappers
(`mdi`, `state`, ...) ignore its return value and come back normally. `command.serial` is
then still the previous command's. `SendCommand` reads `command.serial` before and after the
handler, inside the lock hold; if the handler returned normally and the value did not change,
it answers `RCS_ERROR` (`COMMAND_NOT_ACCEPTED_MESSAGE`), sets no `linuxcnc_serial`, records
no dispatch and adds nothing to `_issued_linuxcnc_serials`. Keep this check after the handler
and before `_record_dispatch`.
- The 5 second give-up above is a different case: the write succeeded, so the serial changed,
  and the command counts as dispatched.
- First command after connecting: `_connect` makes a new `linuxcnc.command()`, whose `serial`
  starts at 0 (the object is zero-filled and `Command_init` does not set it). LinuxCNC's
  serials come from the command buffer's write counter, which is past 0 after the first write
  of a LinuxCNC session, so a failed first write (0 before, 0 after) is refused and a
  successful one is not. The only false refusal would be a new serial equal to the previous
  value, which needs the 32-bit counter to wrap exactly onto it.
- What is detected is only "no message was taken at all". For the multi-message handlers
  (coolant, set_limit, override_config, program_options) and for `program_open` (the binding
  sends `EMC_TASK_PLAN_CLOSE`, then `EMC_TASK_PLAN_OPEN`), one message silently dropped while
  another is taken is answered `RCS_DONE`, with the serial of the last message taken. Telling
  those apart needs a serial comparison around every `self._command` call; not done.
- Not detected in the fallback path (no `.serial`): there is nothing to compare.

**Latest Dispatch Tracking**: after a handler succeeds, and inside the same lock hold,
`SendCommand` calls `_record_dispatch`, which stores a `DispatchRecord` in
`_latest_dispatch_record`. Only the latest dispatch is kept; there is no per-serial map.
- Normally the record holds LinuxCNC's real serial, read from `linuxcnc.command().serial`
  (a read-only member in LinuxCNC 2.8, 2.9 and master). It is read with `getattr` and
  accepted only if it is a real `int`.
- If `.serial` is unavailable, the record instead holds `stat.echo_serial_number` sampled
  *before* the handler ran (`_sample_echo_serial_for_fallback`). It has to be before:
  LinuxCNC's send blocks until the echo serial catches up, so a later sample never advances.
- A dispatch that failed before sending anything does not replace the record. A handler that
  fails *after* sending a message (the multi-message handlers: coolant, set_limit,
  override_config, program_options) does: `SendCommand` compares `command.serial` before and
  after, and records the message that went out, because LinuxCNC is now working on it.
- In the fallback path a failed pre-dispatch poll is not swallowed: the command is refused
  with `RCS_ERROR` rather than dispatched without a record. A partial dispatch cannot be
  detected in the fallback path.

**Connecting**: `_connect()` holds the lock throughout (as it always did). It creates the
new `stat`, `command` and `error_channel` in locals and polls the new `stat` first. Only if
all of that succeeds does it publish the three channels, reset `_latest_dispatch_record` to
`None` and increment `_connection_generation`. A failed attempt changes nothing.

**WaitComplete**: waits on the latest command this server dispatched and **ignores
`request.serial`** (legacy semantics, same as 1.0.0; the serial is only echoed back). Do not
make it target a specific serial: that changes results for existing clients (after send A,
send B, a wait on A's serial must report B).
It does not call `linuxcnc.command().wait_complete()` and does not hold the lock while
waiting. It takes `_latest_dispatch_record` once, then loops: take the lock, `stat.poll()`,
read `echo_serial_number` and `state`, release, decide in `_completion_state`, sleep
`WAIT_COMPLETE_POLL_INTERVAL` (50 ms). An estop sent meanwhile is not blocked.
Because the target is taken once, at the start, a command sent by another client during the
wait is not followed: the wait returns `RCS_DONE` (rule 2) once LinuxCNC has moved past its
target, even if the newer command is still running. In 1.0 the second command queued behind
the wait, so this is a difference in results, and the changelog says so.
The rule in `_completion_state`, checked in this order. Rules 2 to 4 mirror the comparison in
LinuxCNC's own `wait_complete` (`emcWaitCommandComplete` in `emcmodule.cc`). Rule 1 is this
server's own: the native function has no such shortcut, but nothing dispatched through this
server means there is nothing of ours to wait for.
1. No record at all (nothing sent since connecting, or a reconnect reset it):
   `RCS_DONE` immediately, whatever `state` is.
2. Record with a LinuxCNC serial, echo serial **past** it: `RCS_DONE` immediately, whatever
   `state` is. `state` now belongs to a later command, so it says nothing about the awaited
   one, which may still be executing or may have failed.
3. Record with a LinuxCNC serial, echo serial **equal** to it: `state` if it is `RCS_DONE`
   or `RCS_ERROR`; keep waiting while it is `RCS_EXEC`.
4. Record with a LinuxCNC serial, echo serial **behind** it: keep waiting.
5. Fallback record (no `.serial`): `state` once the echo serial is past the pre-dispatch
   sample and `state` is `RCS_DONE` or `RCS_ERROR`; otherwise keep waiting.

"Past", "equal" and "behind" come from `_signed_serial_difference`, a signed 32-bit modular
difference, in rules 2 to 5. LinuxCNC's serial is a C `int` and LinuxCNC compares with
`int serial_diff = echo_serial_number - serial`, so the order stays right across the wrap
from 2147483647 to -2147483648. Do not compare two LinuxCNC serials with `<` or `>`.

**Targeted wait**: if `request.linuxcnc_serial` is set (`HasField`, so 0 counts), the wait
uses a `DispatchRecord` built from that value instead of `_latest_dispatch_record`, and rules
2-4 apply to it. The value must be in `_issued_linuxcnc_serials` (a deque of the last
`ISSUED_SERIALS_REMEMBERED` = 4096 serials `_record_dispatch` returned, emptied by every
`_connect()`); otherwise the wait returns `RCS_ERROR` at once. That is what stops a serial
from before a reconnect being compared with a restarted LinuxCNC's counter. It is a check on
the number only: if the new connection has itself issued the same number, an old request
passes and follows the new command. A hard guarantee would need a connection token in the
response and the request (additive; not done). A generation change during a targeted wait returns `RCS_ERROR` ("Reconnected
to LinuxCNC during the wait…") instead of re-targeting, because a restarted LinuxCNC counts
serials from the start again. This is the additive way to wait on one command's serial; the
untargeted wait keeps the legacy semantics above.

What a targeted wait proves: no more than rule 2 allows. Once any later command has been
echoed (from this server or another client of the same LinuxCNC, for example a feed override
sent during a long MDI move), the answer is `RCS_DONE` although the targeted command may
still be executing or may have failed. It is exact only while nothing else is sent to
LinuxCNC. This is LinuxCNC's own `wait_complete` rule and is kept on purpose; the docs tell
clients to read the status (`task.interp_state`, `trajectory.inpos`,
`trajectory.current_vel`) to know a motion finished. Never document the targeted wait as
proof of one command's outcome. For multi-message commands the serial is the last message
taken, so the wait covers that message. Residual, not fixed: a reconnect between a wait's
last poll and its response is not seen, so the response can still be `RCS_DONE`.

Other behaviour:
- Each poll compares `_connection_generation` with the value seen when the record was
  taken; if it changed, the wait drops its stale record and takes
  `_latest_dispatch_record` again (rule 1 applies if nothing was sent since).
- Timeout (default `DEFAULT_WAIT_COMPLETE_TIMEOUT` = 5 s) or a cancelled RPC returns
  `RCS_EXEC` with an empty message. `linuxcnc.error` returns `RCS_ERROR` with its text.
- `request.timeout` of 0, a negative number or NaN means the default (NaN fails the `> 0`
  test). An infinite timeout is allowed: the wait then ends when the command finishes or the
  client goes away. A gone client is noticed between polls and between attempts at the lock
  (next point), so within about `LOCK_ACQUIRE_SLICE` plus one poll.
- `_acquire_lock_until(deadline, context)` takes the lock in slices of at most
  `LOCK_ACQUIRE_SLICE` (0.1 s). After a slice that timed out it gives up if the deadline has
  passed or `context.is_active()` is false, and otherwise tries again. So a cancelled RPC
  gives up its worker thread even while another RPC holds the lock. The slices also keep the
  timeout below `threading.TIMEOUT_MAX`, above which `Lock.acquire` raises `OverflowError`
  (an infinite deadline used to surface as gRPC `UNKNOWN`).
- Every lock acquisition in WaitComplete goes through `_acquire_lock_until`: taking the
  record, and each poll. If another RPC holds the lock past the deadline, or until the client
  has gone, the wait returns `RCS_EXEC` with an empty message, like any timeout, so it cannot
  overrun its timeout behind a slow lock holder. The first slice is always tried, so a free
  lock is still taken at or past the deadline and for an RPC that is already cancelled.

**Errors**: `LinuxCNCStatus.errors` is always empty (`linuxcnc_mapper.py` sets `errors=[]`).
Error messages reach clients only through `StreamErrors`, which calls `error_channel.poll()`
and so consumes messages from the single NML error queue, taking them away from the GUI.

**Security**: the server has no authentication or TLS. Docs tell users to bind to
`127.0.0.1` and use an SSH tunnel, or to run only on a trusted LAN (`docs/content/server.md`).

**Input Validation**: Handler methods validate indices before dispatching to LinuxCNC:
- `_validate_joint_index(index, is_joint)` checks bounds against `stat.joint`/`stat.axis`; index -1 is only valid for joint operations (home/unhome all).
- `_validate_spindle_index(index)` checks bounds against `stat.spindle`.
- `_validate_nc_path(filename)` resolves paths relative to the NC files directory and rejects path traversal, null bytes, and empty filenames. Used by both `_handle_program_cmd` and the file management RPCs.
- MDI commands reject empty strings, null bytes and commands over `MAX_MDI_COMMAND_LENGTH` (10000 characters). The full command is logged at INFO after it is sent (and its `repr` at DEBUG on arrival).

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
- **Go**: `packages/go/*.pb.go` (package `pb`; `go.mod` and the `client.go` wrapper, package `linuxcnc`, which re-exports these types, are at the repo root)
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

The stream examples run under `timeout 5` and never end by themselves, so exit status 124
(stopped by the timeout) is the only result that passes. Any other status fails the job,
including 0: an example that stops early without an error has still lost its stream.

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
| [docs/content/server-setup.md](docs/content/server-setup.md) | Installing and starting the server |
| [docs/content/server.md](docs/content/server.md) | Server configuration and setup |
| [docs/content/api-reference.md](docs/content/api-reference.md) | Complete API documentation |
| [docs/content/examples.md](docs/content/examples.md) | Examples guide and walkthroughs |
| [docs/content/tutorial.md](docs/content/tutorial.md) | Step-by-step tutorial |
| [docs/content/e2e-testing.md](docs/content/e2e-testing.md) | E2E testing with LinuxCNC simulator |
| [docs/content/using-with-claude.md](docs/content/using-with-claude.md) | linuxcnc-mcp, an MCP server built on this API |

### Local docs development

```bash
make docs-serve    # Downloads theme + starts live-reload server at localhost:1313
make docs-build    # Production build to docs/public/
```

The Hugo theme (hugo-book) is downloaded at build time and gitignored. The GitHub Actions workflow (`.github/workflows/docs.yml`) builds and deploys on push to main when `docs/**` changes.
