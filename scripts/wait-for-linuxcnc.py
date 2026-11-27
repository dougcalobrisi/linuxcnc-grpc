#!/usr/bin/env python3
"""Wait for a LinuxCNC instance to be ready.

Polls linuxcnc.stat() until the task state is valid, indicating that
LinuxCNC has finished initializing. Used by CI to gate test execution
on simulator readiness.

Every attempt runs in its own short-lived Python process. A
linuxcnc.stat().poll() tried while LinuxCNC is not up yet leaves the
linuxcnc module unable to connect for the rest of that process ("error
return without exception set" on every later attempt), so retrying in one
process would never succeed once the first attempt had come too early.
"""

import argparse
import subprocess
import sys
import time

# Run in a child process for each attempt. Prints "task_state interp_state".
PROBE_CODE = (
    "import linuxcnc\n"
    "status = linuxcnc.stat()\n"
    "status.poll()\n"
    "print(status.task_state, status.interp_state)\n"
)
PROBE_TIMEOUT_SECONDS = 10.0


def probe_linuxcnc(probe_timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[int, int]:
    """Read (task_state, interp_state) from LinuxCNC in a child process.

    The child is given at most probe_timeout seconds.
    Raises RuntimeError with the child's error text if LinuxCNC can't be read.
    """
    try:
        probe = subprocess.run(
            [sys.executable, "-c", PROBE_CODE],
            capture_output=True,
            text=True,
            timeout=probe_timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"no answer within {probe_timeout:g}s") from None
    if probe.returncode != 0:
        error_lines = probe.stderr.strip().splitlines()
        raise RuntimeError(error_lines[-1] if error_lines else "probe failed")
    task_state_text, interp_state_text = probe.stdout.split()[-2:]
    return int(task_state_text), int(interp_state_text)


def wait_for_linuxcnc(timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Poll LinuxCNC until the instance is responsive.

    Returns True if LinuxCNC became ready, False on timeout. The timeout is
    a deadline for the whole call: no child process is given more than the
    time left, the wait between attempts stops at the deadline, and an answer
    that arrives after the deadline does not count as ready.
    """
    # The clock starts here, so the module check counts towards the timeout.
    start = time.monotonic()
    deadline = start + timeout

    module_check_timeout = min(PROBE_TIMEOUT_SECONDS, timeout)
    try:
        module_check = subprocess.run(
            [sys.executable, "-c", "import linuxcnc"],
            capture_output=True,
            timeout=module_check_timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"ERROR: Importing the linuxcnc module took longer than {module_check_timeout:g}s",
            file=sys.stderr,
        )
        return False
    if module_check.returncode != 0:
        print("ERROR: Cannot import linuxcnc module", file=sys.stderr)
        print("Ensure LinuxCNC is installed and PYTHONPATH includes its modules", file=sys.stderr)
        return False

    attempt = 0

    # LinuxCNC task_state values (from linuxcnc module constants):
    #   0 = UNSPECIFIED (not yet initialized)
    #   1 = STATE_ESTOP
    #   2 = STATE_ESTOP_RESET
    #   3 = STATE_ON
    #   4 = STATE_OFF
    # We wait until task_state is non-zero, meaning LinuxCNC has fully started.

    while True:
        time_remaining = deadline - time.monotonic()
        if time_remaining <= 0:
            break
        attempt += 1
        try:
            task_state, interp_state = probe_linuxcnc(
                probe_timeout=min(PROBE_TIMEOUT_SECONDS, time_remaining)
            )
            finished_at = time.monotonic()
            elapsed = finished_at - start

            if task_state > 0 and finished_at > deadline:
                print(
                    f"Attempt {attempt} ({elapsed:.1f}s): LinuxCNC answered, "
                    f"but after the {timeout:g}s timeout",
                    file=sys.stderr,
                )
                break

            if task_state > 0:
                print(
                    f"LinuxCNC ready (attempt {attempt}, {elapsed:.1f}s): "
                    f"task_state={task_state}, interp_state={interp_state}"
                )
                return True

            print(
                f"Attempt {attempt} ({elapsed:.1f}s): "
                f"task_state={task_state} (not ready yet)",
                file=sys.stderr,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            print(
                f"Attempt {attempt} ({elapsed:.1f}s): {exc}",
                file=sys.stderr,
            )
        time_remaining = deadline - time.monotonic()
        if time_remaining <= 0:
            break
        time.sleep(min(interval, time_remaining))

    print(f"ERROR: LinuxCNC not ready after {timeout:g}s", file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser(description="Wait for LinuxCNC to be ready")
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds to wait (default: 60)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Seconds between poll attempts (default: 0.5)",
    )
    args = parser.parse_args()

    if wait_for_linuxcnc(timeout=args.timeout, interval=args.interval):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
