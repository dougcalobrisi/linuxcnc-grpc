#!/usr/bin/env python3
"""Wait for a LinuxCNC instance to be ready.

Polls linuxcnc.stat() until the task state is valid, indicating that
LinuxCNC has finished initializing. Used by CI to gate test execution
on simulator readiness.
"""

import argparse
import sys
import time


def wait_for_linuxcnc(timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Poll linuxcnc.stat() until the instance is responsive.

    Returns True if LinuxCNC became ready, False on timeout.
    """
    try:
        import linuxcnc
    except ImportError:
        print("ERROR: Cannot import linuxcnc module", file=sys.stderr)
        print("Ensure LinuxCNC is installed and PYTHONPATH includes its modules", file=sys.stderr)
        return False

    start = time.monotonic()
    attempt = 0

    # LinuxCNC task_state values (from linuxcnc module constants):
    #   0 = UNSPECIFIED (not yet initialized)
    #   1 = STATE_ESTOP
    #   2 = STATE_ESTOP_RESET
    #   3 = STATE_ON
    #   4 = STATE_OFF
    # We wait until task_state is non-zero, meaning LinuxCNC has fully started.

    while time.monotonic() - start < timeout:
        attempt += 1
        try:
            stat = linuxcnc.stat()
            stat.poll()
            task_state = stat.task_state
            interp_state = stat.interp_state
            elapsed = time.monotonic() - start

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
        time.sleep(interval)

    print(f"ERROR: LinuxCNC not ready after {timeout}s", file=sys.stderr)
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
