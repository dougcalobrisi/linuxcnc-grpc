"""
The command helper: a child process that owns ``linuxcnc.command()``.

The server starts this file as a script (see command_helper.py) and never
imports it. A ``linuxcnc.command()`` method writes its message to LinuxCNC
and then waits, for up to 5 seconds and without releasing the interpreter
lock, until LinuxCNC's task has echoed it. Here that wait happens in a
process of its own, so it cannot stall the server's threads.

Protocol: one JSON object per line.

* First line written: ``{"ready": true, "pid": 123, "serial": 42}`` once
  the command channel is open, or ``{"ready": false, "error": "..."}``
  (the process then exits with status 1). ``serial`` is ``command.serial``,
  or null when the bindings do not have it.
* Request, on stdin: ``{"id": 7, "method": "mode", "args": [3]}``.
* Reply: ``{"id": 7, "ok": true, "serial": 43}``, or ``"ok": false`` with
  ``error_type``, ``is_linuxcnc_error`` and ``error``. ``serial`` is read
  after the call either way.

Replies go to the descriptor the process was started with as standard
output. Descriptor 1 itself is pointed at standard error before the
linuxcnc module is loaded, so nothing its C libraries print can land
between two replies.

The process ends when standard input reaches end of file, which is what it
sees when the server closes the pipe or dies. It ignores SIGTERM and
SIGINT: a signal sent to the server's whole process group or control group
must not end it in the middle of a write to LinuxCNC, and the server ends
it through the pipe anyway.
"""

import json
import os
import signal
import sys


def _plain_int_or_none(value):
    """The value if it is a real int (not a bool), else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def main() -> int:
    for ignored_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(ignored_signal, signal.SIG_IGN)

    # A script's own directory comes first on the module path. That is the
    # server package here, and none of its modules is meant to be found as
    # a top-level module by this process.
    script_directory = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.abspath(sys.path[0]) == script_directory:
        del sys.path[0]

    reply_stream = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)

    def send_reply(reply: dict) -> None:
        reply_stream.write(json.dumps(reply) + "\n")
        reply_stream.flush()

    def command_serial():
        return _plain_int_or_none(getattr(command, "serial", None))

    try:
        import linuxcnc

        # The poll is the connection check. A process in which it failed
        # can never connect later, so this one ends and is replaced.
        linuxcnc.stat().poll()
        command = linuxcnc.command()
    except BaseException as connect_error:
        send_reply(
            {"ready": False, "error": f"{type(connect_error).__name__}: {connect_error}"}
        )
        return 1

    send_reply({"ready": True, "pid": os.getpid(), "serial": command_serial()})

    request_stream = sys.stdin.buffer
    while True:
        request_line = request_stream.readline()
        if not request_line.endswith(b"\n"):
            # End of file. A line without its newline is a request the
            # server did not finish writing, and is not acted on.
            return 0
        request = json.loads(request_line)
        method_name = request["method"]
        try:
            if method_name.startswith("_"):
                raise AttributeError(f"not a command method: {method_name}")
            getattr(command, method_name)(*request["args"])
            reply = {"ok": True}
        except Exception as call_error:
            reply = {
                "ok": False,
                "error_type": type(call_error).__name__,
                "is_linuxcnc_error": isinstance(call_error, linuxcnc.error),
                "error": str(call_error),
            }
        reply["id"] = request["id"]
        reply["serial"] = command_serial()
        send_reply(reply)


if __name__ == "__main__":
    sys.exit(main())
