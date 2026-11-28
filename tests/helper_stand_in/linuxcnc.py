"""
Stand-in for the ``linuxcnc`` module, for the command helper process only.

The tests start the real helper script (command_helper_process.py) with
this directory first on PYTHONPATH, so the helper imports this file instead
of LinuxCNC's module and the tests need no LinuxCNC. The test process never
imports it.

The directory named by STAND_IN_LINUXCNC_DIRECTORY holds what a test uses
to steer and observe the helper:

* ``behaviour.json``: ``{"<method name>": "<behaviour>"}``, read again on
  every call. ``"*"`` names every method without an entry of its own, and
  ``"stat.poll"`` the connection check made at startup. Behaviours:
  ``write_fails`` (return normally, serial unchanged, as LinuxCNC does when
  its write fails), ``raise_linuxcnc_error``, ``raise_value_error``,
  ``print_to_stdout`` (write noise to descriptor 1, then succeed),
  ``block`` (see below) and ``no_serial`` (only under ``"command"``: the
  command object has no ``serial`` attribute, like old bindings).
* ``calls.log``: one JSON line ``[process id, method, arguments]`` per call.
* ``serial``: the last serial handed out. Like LinuxCNC's, the counter is
  shared by every command object, so it goes on across helper restarts.
* ``entered`` and ``release``: two FIFOs for ``block``. The call writes its
  method name to ``entered`` and then waits for a line on ``release``:
  ``go`` lets it succeed, ``exit`` ends the process without a reply.
"""

import json
import os

CONTROL_DIRECTORY = os.environ["STAND_IN_LINUXCNC_DIRECTORY"]
FIRST_SERIAL = 101


class error(Exception):
    pass


def _behaviour_of(name: str) -> str:
    try:
        with open(os.path.join(CONTROL_DIRECTORY, "behaviour.json")) as behaviour_file:
            behaviours = json.load(behaviour_file)
    except FileNotFoundError:
        return ""
    return behaviours.get(name, behaviours.get("*", "") if "." not in name else "")


def _next_serial() -> int:
    serial_path = os.path.join(CONTROL_DIRECTORY, "serial")
    try:
        with open(serial_path) as serial_file:
            serial = int(serial_file.read()) + 1
    except FileNotFoundError:
        serial = FIRST_SERIAL
    with open(serial_path, "w") as serial_file:
        serial_file.write(str(serial))
    return serial


class stat:
    def poll(self):
        if _behaviour_of("stat.poll") == "raise_linuxcnc_error":
            raise error("emcStatusBuffer invalid err=3")


class command:
    def __init__(self):
        if _behaviour_of("command") != "no_serial":
            self.serial = 0

    def __getattr__(self, method_name):
        if method_name.startswith("_") or method_name == "serial":
            raise AttributeError(method_name)

        def method(*arguments):
            with open(os.path.join(CONTROL_DIRECTORY, "calls.log"), "a") as call_log:
                call_log.write(json.dumps([os.getpid(), method_name, list(arguments)]) + "\n")
            behaviour = _behaviour_of(method_name)
            if behaviour == "block":
                with open(os.path.join(CONTROL_DIRECTORY, "entered"), "w") as entered:
                    entered.write(method_name + "\n")
                with open(os.path.join(CONTROL_DIRECTORY, "release")) as release:
                    if release.readline().strip() == "exit":
                        os._exit(3)
            elif behaviour == "print_to_stdout":
                os.write(1, b"noise from a C library\n")
            elif behaviour == "raise_linuxcnc_error":
                raise error(f"{method_name} failed in LinuxCNC")
            elif behaviour == "raise_value_error":
                raise ValueError(f"{method_name}: bad argument")
            elif behaviour == "write_fails":
                return
            if hasattr(self, "serial"):
                self.serial = _next_serial()

        return method
