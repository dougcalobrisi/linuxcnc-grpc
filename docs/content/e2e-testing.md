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
- **WaitComplete** — blocking until commands finish, timeout behavior
- **G-code execution** — upload/open/run, pause/resume, abort, single-step
- **Jogging** — incremental and continuous jog with stop
- **Coolant** — mist and flood on/off
- **Rapid rate & max velocity** — override scales
- **Override config** — feed/spindle override enable/disable
- **Program options** — optional stop, block delete
- **Negative paths** — empty MDI, wrong mode, invalid axis, path traversal, duplicate upload
- **Streaming** — StreamStatus, StreamErrors
- **HAL** — GetSystemStatus, QueryPins/Signals/Components/Params, GetValue, StreamStatus, SendCommand, WatchValues
- **File management** — upload/list/delete cycle

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
