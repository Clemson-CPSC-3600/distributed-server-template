"""The harness's selector and its idea of "quiet", on real sockets.  Not graded.

Every wait here is on a condition with a generous bound, so these pass on any
machine however loaded.
"""

import selectors
import socket
import threading
import time

import pytest

from crc_support.quiescence import CountingSocket, QuietSelector, Traffic, wait_for_quiet

DEADLINE = 60.0


class Connection:
    def __init__(self):
        self.write_buffer = b""


class FakeServer:
    """Enough of a server for wait_for_quiet to examine."""
    server_name = "fake"

    def __init__(self, sel):
        self.sel = sel


class LiveThread:
    @staticmethod
    def is_alive():
        return True


@pytest.fixture
def pair():
    raw_left, raw_right = socket.socketpair()
    left = CountingSocket(fileno=raw_left.detach())
    right = CountingSocket(fileno=raw_right.detach())
    left.setblocking(False)
    right.setblocking(False)
    Traffic.reset()
    yield left, right
    left.close()
    right.close()


def _select_until(sel, predicate):
    end = time.monotonic() + DEADLINE
    while time.monotonic() < end:
        ready = sel.select()
        if predicate(ready):
            return ready
    pytest.fail("the selector never reported the expected event")


def test_empty_buffer_is_reported_writable_once_per_registration(pair):
    sel = QuietSelector()
    data = Connection()
    key = sel.register(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, data)
    assert _select_until(sel, bool) == [(key, selectors.EVENT_WRITE)]
    assert sel.select() == []
    new_key = sel.modify(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, Connection())
    assert _select_until(sel, bool) == [(new_key, selectors.EVENT_WRITE)]
    assert sel.select() == []
    sel.close()


def test_read_only_socket_gets_no_write_report(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ, Connection())
    assert sel.select() == []
    sel.close()


def test_not_quiet_until_the_empty_report_is_delivered(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, Connection())
    sel.idle = True
    assert not wait_for_quiet([_entry(sel)], 0.05, 0)
    sel.close()


def test_empty_sends_are_counted(pair):
    pair[1].send(b"")
    pair[1].sendall(b"")
    assert Traffic.empty_sends == 2
    pair[1].send(b"x")
    assert Traffic.empty_sends == 2


def test_socket_with_bytes_queued_is_reported_writable(pair):
    sel = QuietSelector()
    data = Connection()
    data.write_buffer = b"x"
    key = sel.register(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, data)
    assert _select_until(sel, bool) == [(key, selectors.EVENT_WRITE)]
    sel.close()


def test_incoming_bytes_are_reported_readable(pair):
    sel = QuietSelector()
    key = sel.register(pair[0], selectors.EVENT_READ, Connection())
    pair[1].send(b"x")
    assert _select_until(sel, bool) == [(key, selectors.EVENT_READ)]
    sel.close()


def test_data_without_a_write_buffer_behaves_like_any_selector(pair):
    sel = QuietSelector()
    key = sel.register(pair[0], selectors.EVENT_WRITE, "some other design")
    assert _select_until(sel, bool) == [(key, selectors.EVENT_WRITE)]
    sel.close()


def test_harness_uses_of_the_selector_still_work(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ, None)
    sel.modify(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, Connection())
    assert len(sel.get_map()) == 1
    assert list(sel._fd_to_key.keys()) == [pair[0].fileno()]
    sel.unregister(pair[0])
    sel.close()


def _entry(sel):
    return {"app": FakeServer(sel), "thread": LiveThread()}


def _park(sel):
    """Put the selector in the state of a host waiting in select()."""
    while sel.select():
        pass
    assert sel.idle


def test_quiet_when_nothing_is_happening(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, Connection())
    _park(sel)
    assert wait_for_quiet([_entry(sel)], DEADLINE, 0)
    sel.close()


def test_not_quiet_until_the_host_first_reaches_select(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ, Connection())
    assert not wait_for_quiet([_entry(sel)], 0.05, 0)
    sel.close()


def test_not_quiet_with_bytes_queued(pair):
    sel = QuietSelector()
    data = Connection()
    sel.register(pair[0], selectors.EVENT_READ | selectors.EVENT_WRITE, data)
    _park(sel)
    data.write_buffer = b"x"
    assert not wait_for_quiet([_entry(sel)], 0.05, 0)
    sel.close()


def test_count_balances_only_once_everything_sent_is_received(pair):
    pair[1].send(b"abc")
    assert not Traffic.in_balance()
    end = time.monotonic() + DEADLINE
    received = 0
    while received < 3 and time.monotonic() < end:
        try:
            received += len(pair[0].recv(10))
        except BlockingIOError:
            time.sleep(0.001)
    assert received == 3
    assert Traffic.in_balance()


def test_sendall_counts_everything(pair):
    pair[1].sendall(b"abcd")
    assert Traffic.sent == 4


def test_not_quiet_with_bytes_in_flight_even_if_every_host_looks_idle(pair):
    """The case looking at sockets cannot see: bytes inside TCP on their way over."""
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ, Connection())
    _park(sel)
    pair[1].send(b"x")
    assert not wait_for_quiet([_entry(sel)], 0.05, 0)
    sel.close()


def test_not_quiet_while_a_host_is_working_on_what_it_read(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ, Connection())
    pair[1].send(b"x")
    _select_until(sel, bool)
    pair[0].recv(10)            # no bytes left anywhere, but the host is not done
    assert not sel.idle
    assert not wait_for_quiet([_entry(sel)], 0.05, 0)
    sel.close()


def test_quiet_after_a_host_finishes_its_work(pair):
    sel = QuietSelector()
    sel.register(pair[0], selectors.EVENT_READ, Connection())
    pair[1].send(b"x")
    stop = threading.Event()

    def host():
        while not stop.is_set():
            for key, _mask in sel.select():
                key.fileobj.recv(10)

    worker = threading.Thread(target=host)
    worker.start()
    try:
        assert wait_for_quiet([_entry(sel)], DEADLINE, 0)
    finally:
        stop.set()
        worker.join(DEADLINE)
    sel.close()


def test_crashed_server_does_not_block_quiet():
    class Dead:
        @staticmethod
        def is_alive():
            return False

    assert wait_for_quiet([{"app": FakeServer(None), "thread": Dead()}], DEADLINE, 0)


def test_other_selector_falls_back_to_the_old_fixed_wait():
    sel = selectors.SelectSelector()
    start = time.monotonic()
    assert wait_for_quiet([_entry(sel)], DEADLINE, 0.05)
    assert time.monotonic() - start >= 0.05
    sel.close()
