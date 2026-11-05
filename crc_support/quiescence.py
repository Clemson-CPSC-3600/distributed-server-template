"""Let scenarios run as fast as the network does, on any machine.

The tests used to sleep for fixed times and hope the network had finished, while
every host spun at full speed, because a socket with room in its send buffer is
almost always writable.  That made correct servers fail on busy machines.  Two
things here replace the guessing.  Neither asks anything new of your server.

``QuietSelector`` reports a socket as writable only when its connection has bytes
in ``write_buffer``, so a host with nothing to do really does sleep in select().
The exception is one report with an empty buffer each time a socket is registered
or modified.  A real selector does that constantly, and a server has to cope with
it by not sending, which ``CountingSocket`` checks by counting empty sends.

``wait_for_quiet`` waits until the network has finished, rather than for a time.
The network is quiet when every host is parked waiting for input, nothing is
queued to send, and every byte sent so far has been received.  That last check
has to be a count.  Looking at sockets is not enough, because after send() returns
TCP can hold bytes where neither end's socket shows them.  ``CountingSocket``
keeps the count, which no amount of machine load can make wrong.
"""

import select
import selectors
import socket
import threading
import time

# Sleep in select() for at most this long before looking at the world again.  It
# bounds how quickly a host notices it has been told to shut down.
POLL_SECONDS = 0.02

CHECK_INTERVAL_SECONDS = 0.002


class Traffic:
    """Bytes sent and received by every socket in this scenario."""

    _lock = threading.Lock()
    sent = 0
    received = 0
    empty_sends = 0

    @classmethod
    def reset(cls):
        with cls._lock:
            cls.sent = 0
            cls.received = 0
            cls.empty_sends = 0

    @classmethod
    def add_empty_send(cls):
        with cls._lock:
            cls.empty_sends += 1

    @classmethod
    def add_sent(cls, count):
        with cls._lock:
            cls.sent += count

    @classmethod
    def add_received(cls, count):
        with cls._lock:
            cls.received += count

    @classmethod
    def in_balance(cls):
        with cls._lock:
            return cls.sent == cls.received


class CountingSocket(socket.socket):
    """A normal socket that also keeps the count in ``Traffic``.

    A server using a call not counted here, such as recv_into(), loses nothing: the
    count stays out of balance, so its scenario uses the old fixed waits.
    """

    def send(self, data, *args, **kwargs):
        if len(data) == 0:
            Traffic.add_empty_send()
        count = super().send(data, *args, **kwargs)
        Traffic.add_sent(count)
        return count

    def sendall(self, data, *args, **kwargs):
        if len(data) == 0:
            Traffic.add_empty_send()
        super().sendall(data, *args, **kwargs)
        Traffic.add_sent(len(data))

    def recv(self, *args, **kwargs):
        data = super().recv(*args, **kwargs)
        Traffic.add_received(len(data))
        return data


class QuietSelector(selectors.SelectSelector):
    """A selector that stays asleep until a host actually has something to do."""

    def __init__(self):
        super().__init__()
        self.idle = False       # True while this host is waiting for input
        self.generation = 0     # Counts the times this host has been given work
        self.pending_probes = set()  # Sockets still owed one report with an empty buffer

    def register(self, fileobj, events, data=None):
        key = super().register(fileobj, events, data)
        if events & selectors.EVENT_WRITE:
            self.pending_probes.add(key.fd)
        return key

    def unregister(self, fileobj):
        key = super().unregister(fileobj)
        self.pending_probes.discard(key.fd)
        return key

    def modify(self, fileobj, events, data=None):
        key = super().modify(fileobj, events, data)
        if events & selectors.EVENT_WRITE:
            self.pending_probes.add(key.fd)
        return key

    def _wants_write(self, key):
        if not key.events & selectors.EVENT_WRITE:
            return False
        if key.fd in self.pending_probes:
            return True
        # Data without a write_buffer is not ours to interpret: report it the way
        # any other selector would.
        return not hasattr(key.data, "write_buffer") or bool(key.data.write_buffer)

    def select(self, timeout=None):
        keys = list(self.get_map().values())
        readers = [key.fd for key in keys if key.events & selectors.EVENT_READ]
        writers = [key.fd for key in keys if self._wants_write(key)]

        # Everything below flips idle off before the caller can read a byte, so
        # a host seen idle with no unread bytes truly has no work.
        self.idle = True
        if readers or writers:
            readable, writable, _ = select.select(readers, writers, [], POLL_SECONDS)
        else:
            time.sleep(POLL_SECONDS)    # select() with no sockets fails on Windows
            readable, writable = [], []

        ready = []
        for key in keys:
            events = 0
            if key.fd in readable:
                events |= selectors.EVENT_READ
            if key.fd in writable:
                events |= selectors.EVENT_WRITE
                if not getattr(key.data, "write_buffer", b""):
                    self.pending_probes.discard(key.fd)
            if events:
                ready.append((key, events))
        if ready:
            self.idle = False
            self.generation += 1
        return ready


def _snapshot(threads):
    """Each host's generation if the whole network looks quiet right now, else None.

    Raises TypeError if a live server is not using a QuietSelector.
    """
    if not Traffic.in_balance():
        return None

    generations = []
    for entry in threads:
        app, thread = entry["app"], entry["thread"]

        if hasattr(app, "server_name"):
            if not thread.is_alive():
                continue                # it crashed or was shut down
            sel = getattr(app, "sel", None)
            if not isinstance(sel, QuietSelector):
                raise TypeError("server is not using QuietSelector")
            if not sel.idle or sel.pending_probes:
                return None
            selector_map = sel.get_map()
            if selector_map is None:
                return None             # it is closing down right now
            for key in list(selector_map.values()):
                if getattr(key.data, "write_buffer", None):
                    return None
            generations.append(sel.generation)
        else:
            if thread.is_alive():
                return None             # still connecting and registering
            if app.request_terminate:
                continue
            if not app.idle:
                return None
            generations.append(app.generation)

    return tuple(generations)


def wait_for_quiet(threads, deadline_seconds, fallback_seconds):
    """Wait until the network is quiet.  Returns False if the deadline passed first.

    The network counts as quiet only when two checks in a row agree, because hosts
    cannot all be checked at the same instant: a host seen busy, then idle, may have
    just sent bytes to one already checked.  The second check would see them.

    A server built on some other selector cannot be checked, so this falls back to
    sleeping for ``fallback_seconds``, the time the scenario always allowed.
    """
    threads = list(threads)
    deadline = time.monotonic() + deadline_seconds
    previous = None
    while time.monotonic() < deadline:
        try:
            current = _snapshot(threads)
        except TypeError:
            time.sleep(fallback_seconds)
            return True
        except (OSError, ValueError, RuntimeError, KeyError):
            current = None              # a host changed its sockets mid-check
        if current is not None and current == previous:
            return True
        previous = current
        time.sleep(CHECK_INTERVAL_SECONDS)
    return False


def install():
    """Swap in the quiet selector and counting socket for code imported after this."""
    selectors.DefaultSelector = QuietSelector
    socket.socket = CountingSocket
