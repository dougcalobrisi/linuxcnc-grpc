"""
The server's gRPC health service (grpc.health.v1.Health).

grpcio-health-checking's own HealthServicer is not used. Its default Watch
sends the first answer while holding the servicer's lock and waits there for
gRPC's event thread, and that thread, ending another Watch, runs a close
callback that needs the same lock. A few clients starting and cancelling
Watch at the same time froze the server for good: every RPC timed out and
server.stop() did not return. Its set() sends to every watcher under the same
lock, which deadlocks the same way.

This servicer never sends or waits while holding its lock. set() and
enter_graceful_shutdown() only change a dict and wake the watchers, so they
can be called with other locks held (the LinuxCNC servicer calls the
connection listener with its state lock held) and they never block.

Only the generated messages and stubs of grpcio-health-checking are used
(health_pb2, health_pb2_grpc).
"""

import threading

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

# Most watchers create_server allows with the default 10 worker threads.
MAX_HEALTH_WATCHERS = 4
# How long a watcher waits for a change before it checks again whether its
# call is still active. gRPC wakes it as soon as the call ends; this only
# bounds how long a watcher could outlive its call if that wake-up were lost.
WATCH_ACTIVITY_CHECK_INTERVAL = 5.0  # seconds

WATCHER_LIMIT_DETAILS = (
    "The server's limit of {limit} concurrent health watchers is reached; "
    "use grpc.health.v1.Health/Check instead"
)
WATCH_NOT_AVAILABLE_DETAILS = (
    "Health Watch is not available on this server: it has too few worker "
    "threads to give one to a watcher; use grpc.health.v1.Health/Check instead"
)


def health_watcher_limit(max_workers: int) -> int:
    """
    How many Watch calls may be open at once on a server with this many
    worker threads.

    Every open Watch occupies one worker thread. At most half of them may be
    watchers, and never more than MAX_HEALTH_WATCHERS, so the other RPCs
    always have a thread left. A server with one worker gets 0: a single
    watcher would hold its only thread, and Check and every other RPC would
    queue behind it.
    """
    return max(0, min(MAX_HEALTH_WATCHERS, max_workers // 2))


class _Watcher:
    """One open Watch call: the service it watches and the event that wakes it."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self.wake_event = threading.Event()


class HealthServicer(health_pb2_grpc.HealthServicer):
    """
    Check and Watch for the statuses create_server sets.

    Check answers from the status table; a service name that was never set
    is NOT_FOUND. Watch sends the current status first (SERVICE_UNKNOWN for
    a name not set yet) and then the status whenever it differs from the
    last one sent. Changes that come faster than the watcher looks arrive
    as the newest status alone, and one undone before it looks is not sent.
    After enter_graceful_shutdown() each Watch sends its last status
    (NOT_SERVING) and ends with OK, since nothing can change any more.

    At most max_concurrent_watchers Watch calls are open at once (0 means
    none). One more ends at once with UNIMPLEMENTED: gRPC's client-side
    health checking takes that to mean "health checking is not available",
    treats the server as healthy and does not watch again on that
    connection. Any other code would put the client's channel into
    TRANSIENT_FAILURE, so that client could not use the server at all while
    the others watch.
    """

    def __init__(self, max_concurrent_watchers: int = MAX_HEALTH_WATCHERS):
        if max_concurrent_watchers < 0:
            raise ValueError("max_concurrent_watchers must not be negative")
        self._max_concurrent_watchers = max_concurrent_watchers
        # Guards the three fields below. Held only to read or change them:
        # nothing is sent and nothing waits while it is held.
        self._lock = threading.Lock()
        # Like grpcio-health-checking: the server as a whole starts SERVING.
        self._status_by_service = {"": health_pb2.HealthCheckResponse.SERVING}
        self._watchers = set()
        self._gracefully_shutting_down = False

    @property
    def max_concurrent_watchers(self) -> int:
        return self._max_concurrent_watchers

    def watcher_count(self) -> int:
        """How many Watch calls are open now."""
        with self._lock:
            return len(self._watchers)

    def Check(self, request, context):
        with self._lock:
            status = self._status_by_service.get(request.service)
        if status is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            return health_pb2.HealthCheckResponse()
        return health_pb2.HealthCheckResponse(status=status)

    def Watch(self, request, context):
        watcher = _Watcher(request.service)
        # gRPC runs the callback when the call ends for any reason (cancel,
        # disconnect, deadline, server stop); Event.set never waits. False
        # means the call ended before this handler ran: there is nobody to
        # answer, and registering the watcher would leave it behind for good.
        if not context.add_callback(watcher.wake_event.set):
            return
        with self._lock:
            watcher_accepted = len(self._watchers) < self._max_concurrent_watchers
            if watcher_accepted:
                self._watchers.add(watcher)
        if not watcher_accepted:
            # Outside the lock: abort raises.
            context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                WATCHER_LIMIT_DETAILS.format(limit=self._max_concurrent_watchers)
                if self._max_concurrent_watchers
                else WATCH_NOT_AVAILABLE_DETAILS,
            )
        try:
            last_sent_status = None
            while True:
                # Cleared before the status is read, so a set() that lands
                # after the read wakes the next wait.
                watcher.wake_event.clear()
                with self._lock:
                    status = self._status_by_service.get(
                        watcher.service_name,
                        health_pb2.HealthCheckResponse.SERVICE_UNKNOWN,
                    )
                    shutting_down = self._gracefully_shutting_down
                # The clear() above can also erase the one wake-up gRPC gives
                # when the call ends, so look at the call itself on every
                # pass. Without this a finished call would keep its worker
                # thread and its watcher slot.
                if not context.is_active():
                    return
                if status != last_sent_status:
                    # Sent with no lock held: a send can wait for the client
                    # or for gRPC's event thread.
                    yield health_pb2.HealthCheckResponse(status=status)
                    last_sent_status = status
                if shutting_down:
                    # The status cannot change any more (set() is ignored
                    # now). Ending here gives the worker thread back at once,
                    # so a watcher does not hold up a graceful stop for its
                    # whole grace period.
                    return
                watcher.wake_event.wait(WATCH_ACTIVITY_CHECK_INTERVAL)
        finally:
            with self._lock:
                self._watchers.discard(watcher)

    def set(self, service: str, status) -> None:
        """
        Set the status of a service and wake its watchers.

        Never blocks and never sends, so it may be called with any other lock
        held. Ignored after enter_graceful_shutdown().
        """
        with self._lock:
            if self._gracefully_shutting_down:
                return
            self._status_by_service[service] = status
            watchers_to_wake = [
                watcher for watcher in self._watchers if watcher.service_name == service
            ]
        for watcher in watchers_to_wake:
            watcher.wake_event.set()

    def enter_graceful_shutdown(self) -> None:
        """
        Turn every service that has a status to NOT_SERVING for good.

        Later set() calls are ignored. Never blocks and never sends; each
        watcher sends the change itself and then ends its call.
        """
        with self._lock:
            if self._gracefully_shutting_down:
                return
            for service in self._status_by_service:
                self._status_by_service[service] = health_pb2.HealthCheckResponse.NOT_SERVING
            self._gracefully_shutting_down = True
            watchers_to_wake = list(self._watchers)
        for watcher in watchers_to_wake:
            watcher.wake_event.set()
