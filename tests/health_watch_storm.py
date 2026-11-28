"""
Child process for tests/test_health_service.py: many clients start and cancel
grpc.health.v1.Health/Watch at once while the status changes.

Run on its own so that a server that freezes cannot hang the test suite: the
test kills this process after a hard timeout. Every wait in here is bounded
as well, and a watchdog ends the process with a stack dump of every thread
if the whole run takes longer than OVERALL_TIMEOUT, so the script cannot
hang when run by hand either. Prints one line, RESULT followed by JSON, and
exits with os._exit so that no gRPC thread can keep the process alive.

Usage: python health_watch_storm.py [attempts]
"""

import faulthandler
import json
import os
import sys
import threading
import time
from concurrent import futures

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from linuxcnc_grpc.health_service import HealthServicer, health_watcher_limit

WORKER_THREADS = 10
CANCELLING_THREADS = 8
CHECK_EVERY_ATTEMPTS = 50
# Generous, for a loaded machine: the server answers a Check in about a
# millisecond, and a frozen one never does.
CHECK_TIMEOUT = 10.0  # seconds
THREAD_JOIN_TIMEOUT = 5.0  # seconds
STOP_TIMEOUT = 10.0  # seconds
WATCHERS_GONE_TIMEOUT = 5.0  # seconds
# Below the test's hard timeout (60 s), so the dump reaches its output.
OVERALL_TIMEOUT = 50.0  # seconds


class CountingHealthServicer(HealthServicer):
    """Counts the Watch handlers the server ran, to show the storm reached it."""

    def __init__(self, *arguments, **keyword_arguments):
        super().__init__(*arguments, **keyword_arguments)
        self.watch_handlers_run = 0
        self._count_lock = threading.Lock()

    def Watch(self, request, context):
        with self._count_lock:
            self.watch_handlers_run += 1
        yield from super().Watch(request, context)


def make_health_servicer():
    return CountingHealthServicer(
        max_concurrent_watchers=health_watcher_limit(WORKER_THREADS)
    )


def watch_handlers_run(health_servicer):
    return health_servicer.watch_handlers_run


def open_watcher_count(health_servicer):
    return health_servicer.watcher_count()


def main():
    faulthandler.dump_traceback_later(OVERALL_TIMEOUT, exit=True)
    attempts = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    result = {"attempts": attempts}

    grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=WORKER_THREADS))
    health_servicer = make_health_servicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, grpc_server)
    port = grpc_server.add_insecure_port("127.0.0.1:0")
    grpc_server.start()
    address = f"127.0.0.1:{port}"

    storm_channel = grpc.insecure_channel(address)
    storm_stub = health_pb2_grpc.HealthStub(storm_channel)
    storm_stub.Check(health_pb2.HealthCheckRequest(), timeout=CHECK_TIMEOUT)
    stop_storm = threading.Event()
    # An exception in a storm thread would otherwise end that thread
    # quietly and leave a storm that tests nothing.
    thread_errors = []
    thread_watch_calls = []

    def start_and_cancel_watches():
        watch_calls = 0
        try:
            while not stop_storm.is_set():
                storm_stub.Watch(health_pb2.HealthCheckRequest()).cancel()
                watch_calls += 1
        except Exception as error:
            thread_errors.append(f"{type(error).__name__}: {error}")
        thread_watch_calls.append(watch_calls)

    # A status that changes while watchers come and go is the second way the
    # upstream servicer froze.
    def flip_the_status():
        statuses = (
            health_pb2.HealthCheckResponse.SERVING,
            health_pb2.HealthCheckResponse.NOT_SERVING,
        )
        flip_number = 0
        try:
            while not stop_storm.is_set():
                health_servicer.set("", statuses[flip_number % 2])
                flip_number += 1
                time.sleep(0.001)
        except Exception as error:
            thread_errors.append(f"{type(error).__name__}: {error}")
        result["status_flips"] = flip_number

    storm_threads = [
        threading.Thread(target=start_and_cancel_watches, daemon=True)
        for _ in range(CANCELLING_THREADS)
    ]
    storm_threads.append(threading.Thread(target=flip_the_status, daemon=True))
    for storm_thread in storm_threads:
        storm_thread.start()

    failed_check = None
    attempts_done = 0
    checks_answered = 0
    storm_started = time.monotonic()
    for attempt_number in range(1, attempts + 1):
        watch_call = storm_stub.Watch(health_pb2.HealthCheckRequest())
        if attempt_number % 2:
            time.sleep(0.0005)
        watch_call.cancel()
        attempts_done += 1
        if attempt_number % CHECK_EVERY_ATTEMPTS == 0:
            try:
                storm_stub.Check(health_pb2.HealthCheckRequest(), timeout=CHECK_TIMEOUT)
                checks_answered += 1
            except grpc.RpcError as check_error:
                failed_check = {"attempt": attempt_number, "code": check_error.code().name}
                break
    stop_storm.set()
    result["failed_check"] = failed_check
    result["storm_seconds"] = round(time.monotonic() - storm_started, 2)
    for storm_thread in storm_threads:
        storm_thread.join(THREAD_JOIN_TIMEOUT)
    result["storm_threads_stuck"] = sum(thread.is_alive() for thread in storm_threads)
    result["attempts_done"] = attempts_done
    result["checks_answered"] = checks_answered
    result["thread_errors"] = thread_errors
    result["thread_watch_calls"] = sum(thread_watch_calls)
    result["watch_handlers_run"] = watch_handlers_run(health_servicer)

    # A new client on a new connection.
    with grpc.insecure_channel(address) as fresh_channel:
        try:
            response = health_pb2_grpc.HealthStub(fresh_channel).Check(
                health_pb2.HealthCheckRequest(), timeout=CHECK_TIMEOUT
            )
            result["fresh_check"] = health_pb2.HealthCheckResponse.ServingStatus.Name(
                response.status
            )
        except grpc.RpcError as check_error:
            result["fresh_check"] = check_error.code().name

    if failed_check is None:
        deadline = time.monotonic() + WATCHERS_GONE_TIMEOUT
        while open_watcher_count(health_servicer) and time.monotonic() < deadline:
            time.sleep(0.02)
        result["watchers_left"] = open_watcher_count(health_servicer)

    stop_started = time.monotonic()
    stopping = threading.Thread(target=lambda: grpc_server.stop(0).wait(), daemon=True)
    stopping.start()
    stopping.join(STOP_TIMEOUT)
    result["stop_returned"] = not stopping.is_alive()
    result["stop_seconds"] = round(time.monotonic() - stop_started, 2)

    print("RESULT " + json.dumps(result), flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
