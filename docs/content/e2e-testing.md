---
title: "End-to-End Testing"
weight: 60
---

The e2e test suite (`tests/test_e2e.py`) runs against a real LinuxCNC simulator
instance, validating that the gRPC server works correctly with actual LinuxCNC
state, commands, HAL data, and file management.

## What E2E Tests Cover

The suite has ~50 tests organized into these areas:

- **Status** — version, joints, INI filename, positions, task state
- **State transitions** — estop/recovery, on/off, mode changes
- **Commands** — homing, MDI G-code, feedrate/spindle overrides
- **WaitComplete** — blocking until commands finish, timeout behavior, waiting on one command by its `linuxcnc_serial`
- **G-code execution** — upload/open/run, pause/resume, abort, single-step
- **Jogging** — incremental and continuous jog with stop
- **Errors** — fan-out of one message to two streams, `status.errors`, replay of buffered messages, a real MDI rejection
- **Machine configuration** — `GetMachineConfig` against the simulator's INI file and status
- **Coolant** — mist and flood on/off
- **Rapid rate & max velocity** — override scales
- **Override config** — feed/spindle override enable/disable
- **Program options** — optional stop, block delete
- **Negative paths** — empty MDI, wrong mode, invalid axis, path traversal, duplicate upload
- **Streaming** — StreamStatus, StreamErrors
- **HAL** — GetSystemStatus, QueryPins/Signals/Components/Params, GetValue, StreamStatus, SendCommand, WatchValues
- **File management** — upload/list/delete cycle, reading a file back whole and in chunks, resolving the loaded program

These tests catch issues that mock-based tests cannot:

- Real state machine transitions (estop, on/off, mode changes)
- Actual command execution (homing, MDI G-code, overrides)
- Real HAL pin/signal/component data from the motion controller
- File upload/list/delete against a real filesystem
- Streaming RPCs with live-updating data
- Thread safety under real concurrent access patterns

## CI Workflow

The e2e tests run in `.github/workflows/e2e.yml` on every PR and push to main.

The workflow:
1. Builds LinuxCNC from source in RIP (run-in-place) mode on Ubuntu 24.04
2. Launches Xvfb (virtual framebuffer) for any X11 needs
3. Creates a `headless` display script (a no-op process that sleeps forever) —
   the `linuxcnc` launcher waits for the DISPLAY program to exit and shuts
   everything down when it does, so a persistent headless script keeps components alive
4. Patches `sim/axis/axis.ini` to use `DISPLAY = headless` instead of `axis`
5. Starts LinuxCNC with the patched simulator config
6. Waits for LinuxCNC readiness via `scripts/wait-for-linuxcnc.py`
7. Starts the real gRPC server connected to the simulator
8. Runs `pytest tests/test_e2e.py -m e2e`

## Running Locally

Prerequisites: LinuxCNC simulator installed (`linuxcnc-uspace` package on Debian/Ubuntu).

```bash
# 1. Start LinuxCNC with a sim config
linuxcnc /usr/share/linuxcnc/configs/sim/axis/axis.ini &

# 2. Wait for it to initialize
python3 scripts/wait-for-linuxcnc.py

# 3. Start the gRPC server
uv run python -m linuxcnc_grpc.server --nc-files /tmp/e2e-nc-files &

# 4. Run e2e tests
uv run pytest tests/test_e2e.py -v -m e2e
```

Note: On a non-LinuxCNC machine, you need a virtual display:
```bash
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
```

The server can be started before LinuxCNC (it waits), and
`scripts/wait-for-linuxcnc.py` can be started before LinuxCNC too. Its
`--timeout` covers the whole run, including the first check that the
`linuxcnc` module can be imported.

### In Docker (macOS, Windows, or any machine without LinuxCNC)

Debian 13 packages the LinuxCNC simulator (`linuxcnc-uspace`, 2.9.4, also for
arm64), so the suite can run in a container. From the repository root:

```dockerfile
# Dockerfile.sim
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    linuxcnc-uspace python3 python3-venv python3-pip xvfb netcat-openbsd procps sudo \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m cnc && echo 'cnc ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/cnc
USER cnc
RUN python3 -m venv --system-site-packages /home/cnc/venv \
    && /home/cnc/venv/bin/pip install grpcio grpcio-health-checking protobuf pytest
```

```bash
docker build -t linuxcnc-sim -f Dockerfile.sim .
docker run -d --init --name linuxcnc-sim \
  --cap-add IPC_OWNER --cap-add SYS_NICE --cap-add IPC_LOCK \
  -v "$PWD":/repo:ro linuxcnc-sim sleep infinity
```

Then, inside the container (`docker exec -it linuxcnc-sim bash`), follow the
same steps as the CI workflow: copy the `sim/axis` sample configuration from
`/usr/share/doc/linuxcnc/examples/sample-configs/sim/axis`, create the
`headless` display script, set `DISPLAY = headless` in the copied `axis.ini`,
start `Xvfb`, start `linuxcnc`, and run the server and the tests with
`PYTHONPATH=/repo/src:/repo/packages/python` and the venv's Python.

Three things the container needs:

- `--cap-add IPC_OWNER`: LinuxCNC's realtime helper (`rtapi_app`) runs as
  root and must open shared memory created by the `cnc` user. Docker drops
  that capability by default, and LinuxCNC then fails with
  `rtapi_shmem_new failed due to shmget(...): Permission denied`.
- `--init`: so that stopped LinuxCNC processes are reaped. Without it they
  stay as zombies, and anything that checks whether LinuxCNC is still running
  by looking for its processes is fooled.
- A normal user rather than root, as on a real machine.

## Adding New E2E Tests

1. Add tests to `tests/test_e2e.py`
2. Decorate with `@pytest.mark.e2e`
3. Use the `linuxcnc_stub` or `hal_stub` fixtures for gRPC access
4. Use `wait_for_condition()` instead of fixed `time.sleep()` to avoid flakiness
5. Use `ensure_machine_on()` or the `machine_on` fixture to reset state between tests
6. Use `home_all(stub)` to home joints before tests that require motion
7. Use `wait_complete(stub)` instead of polling when waiting for a command to finish
8. Use `upload_test_file()` / `delete_test_file()` for file management in tests
9. Always restore state (overrides, modes) at the end of tests to avoid affecting later tests

## Mock Tests vs E2E Tests

| Aspect | Mock tests (`test_integration.py`) | E2E tests (`test_e2e.py`) |
|--------|-----------------------------------|--------------------------|
| Server | `tests/mock_server.py` | Real `linuxcnc_grpc.server` |
| LinuxCNC | Not needed | Real simulator instance |
| Speed | Fast (~seconds) | Slower (~minutes) |
| Determinism | Fully deterministic | Timing-dependent |
| Coverage | API shape, serialization | Real behavior, state machines |
