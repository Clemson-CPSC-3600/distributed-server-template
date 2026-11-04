"""Keep the student-facing docs honest.  Not graded: no bundle marker.

Two instructor rules live here.  Student docs must never show working code from
the project (generic ``selectors`` examples are fine), and concrete values in the
docs must match real output, because students trust the docs over their own code.
"""

import re
import socket
import threading
import time
from pathlib import Path

import pytest

from crc_support.ChatMessageParser import ClientRegistrationMessage

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "PROTOCOL.md", "DEBUGGING.md", "TESTING.md", "GRADING.md"]

# Names that only make sense inside this project's server.
PROJECT_CODE = re.compile(
    r"self\.sel\b|hosts_db|first_link_id|write_buffer|adjacent_|ConnectionData|io_device|"
    r"status_updates_log|message_handlers|request_terminate|def handle_|\.handle_\w+\(|"
    r"send_message_to_|broadcast_message_to_"
)
PYTHON_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", DOCS)
def test_no_project_code_in_python_examples(name):
    for block in PYTHON_FENCE.findall(_read(name)):
        found = PROJECT_CODE.findall(block)
        assert not found, f"{name} shows project code ({found}):\n{block}"


@pytest.mark.parametrize("name", DOCS)
def test_project_state_expressions_are_not_spelled_out_in_prose(name):
    assert not re.search(r"self\.\w+\[[^\]]+\]", _read(name)), "indexing project state reads as code"


def test_starter_docstrings_show_no_project_calls():
    for name in ("src/ChatServer.py", "template/ChatServer.py"):
        text = _read(name)
        assert "self.sel.modify(" not in text
        assert "isinstance(self.hosts_db" not in text


def test_starter_and_backup_match():
    assert _read("template/ChatServer.py") == _read("src/ChatServer.py")


@pytest.mark.parametrize("name", DOCS)
def test_no_retired_channels_or_missing_folders(name):
    text = _read(name)
    assert "Classroom" not in text
    assert "correct_logs" not in text
    assert "extra credit" not in text.lower()


@pytest.mark.parametrize("name", DOCS)
def test_relative_links_resolve(name):
    for target in re.findall(r"\]\(([^)#]+\.md)", _read(name)):
        assert (ROOT / target).exists(), f"{name} links to {target}"


def test_worked_bytes_match_the_parser():
    raw = ClientRegistrationMessage.bytes(101, 0, "frodo", "hi")
    assert raw.hex(" ") == "80 00 00 00 65 00 00 00 00 05 00 02 66 72 6f 64 6f 68 69"
    spaced = re.sub(r"\s+", " ", re.search(r"```text\n(80 .*?)\n```", _read("PROTOCOL.md")).group(1))
    assert spaced == raw.hex(" ")


def test_log_excerpts_are_real():
    reference = (ROOT / "crc_support/reference_traces/4_2_Message_One_Hop.txt").read_text(encoding="utf-8")
    excerpts = [
        line for line in _read("DEBUGGING.md").splitlines()
        if line.startswith(("step ", "# step ", "rivendale  "))
    ]
    assert len(excerpts) >= 10
    for line in excerpts:
        assert line in reference, line


def test_worked_example_exchange_is_real():
    reference = (ROOT / "crc_support/reference_traces/4_2_Message_One_Hop.txt").read_text(encoding="utf-8")
    for expected in (
        "step 04  theshire  ServerRegistration source=3 last_hop=0",
        "step 04  grey_havens  ServerRegistration source=1 last_hop=1",
        "step 04  grey_havens  ServerRegistration source=2 last_hop=1",
        "step 04  grey_havens  ClientRegistration source=101 last_hop=1",
        "step 04  rivendale  ServerRegistration source=3 last_hop=1",
    ):
        assert expected in reference
    assert "rivendale  routes  1 via 1, 3 via 1, 101 via 1" in reference
    assert "theshire  routes  2 via 2, 3 via 3, 101 via 101" in reference


def test_topology_table_matches_the_largest_scenarios():
    """The text table and the diagram must both match what the scenarios launch."""
    servers = {}
    for server_id, name, rest in re.findall(
        r"LAUNCHSERVER --id (\d+) --servername (\S+) ([^\n]*)", _read("crc_support/TestCases/2_3_ElevenServers.cfg")
    ):
        parent = re.search(r"--connect_to_host (\S+)", rest)
        servers[name] = {"id": server_id, "parent": parent.group(1) if parent else None, "clients": set()}
    clients = re.findall(
        r"LAUNCHCLIENT --id (\d+) --serverhost (\S+) .*?--username (\S+)",
        _read("crc_support/TestCases/3_4_ElevenServers_EightClients.cfg"),
    )
    for client_id, server, name in clients:
        servers[server]["clients"].add(f"{client_id} `{name}`")
    assert len(servers) == 11 and len(clients) == 8

    protocol = _read("PROTOCOL.md")
    for name, server in servers.items():
        parent = f"{servers[server['parent']]['id']} `{server['parent']}`" if server["parent"] else "none (first server)"
        row = re.search(rf"^\| {server['id']} `{name}` \| (.*?) \| (.*?) \|$", protocol, re.MULTILINE)
        assert row, name
        assert row.group(1) == parent, name
        listed = set() if row.group(2) == "none" else set(row.group(2).split(", "))
        assert listed == server["clients"], name
        for client in server["clients"]:
            client_id = client.split()[0]
            assert re.search(rf"C{client_id}\(\[.*\]\) -\.- S{server['id']}$", protocol, re.MULTILINE), client
        if server["parent"]:
            assert re.search(rf"^\s+S{servers[server['parent']]['id']} --- S{server['id']}$", protocol, re.MULTILINE), name


def test_selector_example_runs():
    """The generic selectors example really does what the doc says it does."""
    block = next(b for b in PYTHON_FENCE.findall(_read("PROTOCOL.md")) if "DefaultSelector" in b)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = block.replace("9000", str(port))
    # Stop the example's loop from outside, as a shutdown flag would.
    source = source.replace("while running:", "while running and not stop.is_set():")
    stop = threading.Event()
    server = threading.Thread(target=exec, args=(source, {"stop": stop}), daemon=True)
    server.start()
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                client = socket.create_connection(("127.0.0.1", port), timeout=30)
                break
            except OSError:
                assert time.monotonic() < deadline, "example never started listening"
                time.sleep(0.05)
        with client:
            client.sendall(b"hello")
            assert client.recv(100) == b"HELLO"
    finally:
        stop.set()
        server.join(30)
    assert not server.is_alive()
