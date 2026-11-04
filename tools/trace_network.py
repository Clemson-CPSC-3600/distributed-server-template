#!/usr/bin/env python3
"""Show what every host in a CRC scenario received, and compare it to a reference.

For each scenario this runs your ``src/ChatServer.py`` through the same harness
the graded tests use, records every message each server and client received and
each server's final routing state, writes that as a readable log under
``trace-logs/``, and reports the earliest difference from a correct network.

    python tools/trace_network.py --list
    python tools/trace_network.py --scenario 4_2_Message_One_Hop
    python tools/trace_network.py --bundle 1
    python tools/trace_network.py                  # every scenario (minutes)

Scenarios run on real sockets and threads, so the order messages arrive in can
vary from run to run, and the spec does not grade that order.  The comparison
therefore treats each host's messages as a collection and uses step numbers only
to say *when* a difference first shows up.

This is a debugging aid, not a grader: ``python run_tests.py`` is the authority.
See DEBUGGING.md.  Instructors regenerate the committed logs with::

    python tools/trace_network.py --write-reference-traces
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import threading
import traceback
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TESTCASES_DIR = REPO_ROOT / "crc_support" / "TestCases"
REFERENCE_DIR = REPO_ROOT / "crc_support" / "reference_traces"
OUTPUT_DIR = REPO_ROOT / "trace-logs"
SOLUTION = REPO_ROOT / "solution" / "ChatServer.py"

# A scenario that outlives this has a host that will not shut down.  The longest
# reference scenario takes about 35 seconds on a heavily loaded machine.
SCENARIO_TIMEOUT_SECONDS = 180

BUNDLE_FILES = {
    1: "test_bundle1_core.py",
    2: "test_bundle2_routing.py",
    3: "test_bundle3_multihop.py",
}

MESSAGE_LINE = re.compile(r"^step (\d+)  (\S+)  (.*)$")
STEP_HEADER = re.compile(r"^# step (\d+): (.*)$")
STATE_HEADER = "# final state"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def describe_message(message) -> str:
    """One readable line of decoded header fields for a parsed CRC message."""
    code = message.message_type
    if code == 0x00:
        return (
            f"ServerRegistration source={message.source_id} last_hop={message.last_hop_id} "
            f"name={message.server_name!r} info={message.server_info!r}"
        )
    if code == 0x80:
        return (
            f"ClientRegistration source={message.source_id} last_hop={message.last_hop_id} "
            f"name={message.client_name!r} info={message.client_info!r}"
        )
    if code == 0x01:
        return (
            f"StatusUpdate source={message.source_id} destination={message.destination_id} "
            f"code=0x{message.status_code:02x} text={message.content!r}"
        )
    if code == 0x81:
        return (
            f"ClientChat source={message.source_id} destination={message.destination_id} "
            f"text={message.content!r}"
        )
    if code == 0x82:
        return f"ClientQuit source={message.source_id} text={message.content!r}"
    return f"Message type=0x{code:02x} source={message.source_id}"


def render_trace(steps: Dict[int, str], events: Iterable[dict], state: Iterable[str]) -> str:
    """Render a scenario as text.  Within a step, lines are sorted for stability."""
    by_step: Dict[int, List[str]] = {number: [] for number in steps}
    for event in events:
        by_step.setdefault(event["step"], []).append(
            f"step {event['step']:02d}  {event['host']}  {event['text']}"
        )

    lines: List[str] = []
    for number in sorted(by_step):
        lines.append(f"# step {number:02d}: {steps.get(number, 'startup')}")
        lines.extend(sorted(by_step[number]))
    lines.append(STATE_HEADER)
    lines.extend(sorted(state))
    return "\n".join(lines) + "\n"


class Trace(NamedTuple):
    steps: Dict[int, str]
    messages: List[tuple]  # (step, host, text)
    state: List[str]


def parse_trace(text: str) -> Trace:
    steps: Dict[int, str] = {}
    messages: List[tuple] = []
    state: List[str] = []
    in_state = False
    for line in text.splitlines():
        if line == STATE_HEADER:
            in_state = True
        elif in_state:
            if line.strip():
                state.append(line)
        else:
            header = STEP_HEADER.match(line)
            if header:
                steps[int(header.group(1))] = header.group(2)
                continue
            message = MESSAGE_LINE.match(line)
            if message:
                messages.append((int(message.group(1)), message.group(2), message.group(3)))
    return Trace(steps, messages, state)


class Difference(NamedTuple):
    step: int  # step the difference first shows up in; state differences sort last
    kind: str  # "missing", "extra", or "state"
    host: str
    text: str
    expected: Optional[str] = None


FINAL_STATE_STEP = 10**6


def compare_traces(reference: Trace, yours: Trace) -> List[Difference]:
    """Every difference between two traces, earliest first.

    Messages are compared per host as a multiset, so arrival order and the step a
    message happened to land in never create a difference of their own.
    """
    differences: List[Difference] = []

    reference_counts = Counter((host, text) for _, host, text in reference.messages)
    your_counts = Counter((host, text) for _, host, text in yours.messages)

    seen: Counter = Counter()
    for step, host, text in sorted(reference.messages):
        key = (host, text)
        seen[key] += 1
        if seen[key] > your_counts[key]:
            differences.append(Difference(step, "missing", host, text))

    seen = Counter()
    for step, host, text in sorted(yours.messages):
        key = (host, text)
        seen[key] += 1
        if seen[key] > reference_counts[key]:
            differences.append(Difference(step, "extra", host, text))

    your_state = {tuple(line.split("  ", 2)[:2]): line for line in yours.state}
    reference_state = {tuple(line.split("  ", 2)[:2]): line for line in reference.state}
    for key, line in reference_state.items():
        if your_state.get(key) != line:
            differences.append(
                Difference(FINAL_STATE_STEP, "state", key[0], your_state.get(key, "(nothing recorded)"), line)
            )
    for key, line in your_state.items():
        if key not in reference_state:
            differences.append(Difference(FINAL_STATE_STEP, "state", key[0], line, "(nothing expected)"))

    return sorted(differences, key=lambda d: (d.step, d.kind, d.host, d.text))


def describe_difference(difference: Difference, steps: Dict[int, str]) -> List[str]:
    if difference.kind == "state":
        return [
            f"final state of {difference.host}:",
            f"  reference: {difference.expected}",
            f"  yours:     {difference.text}",
        ]
    label = steps.get(difference.step, "startup")
    verb = "never received" if difference.kind == "missing" else "received something the reference did not"
    return [
        f"step {difference.step:02d} ({label}): {difference.host} {verb}:",
        f"  {difference.text}",
    ]


# ---------------------------------------------------------------------------
# Running one scenario (happens in a child process)
# ---------------------------------------------------------------------------


def _summarize_command(name: str, args: str) -> str:
    def option(flag: str) -> Optional[str]:
        found = re.search(rf"--{flag} (\"[^\"]*\"|\S+)", args)
        return found.group(1) if found else None

    if name == "LAUNCHSERVER":
        via = option("connect_to_host")
        joined = f", connects to {via}" if via else ", first server"
        return f"start server {option('servername')} (id {option('id')}{joined})"
    if name == "LAUNCHCLIENT":
        return f"start client {option('username')} (id {option('id')}) on {option('serverhost')}"
    if name == "CLIENTCOMMAND":
        command = args.split("--command", 1)[-1].strip()
        return f"{option('username')} runs {command}"
    if name == "KILL":
        return f"shut down {args.strip()}"
    return f"{name} {args}".strip()


def _state_lines(servers: list, clients: list) -> List[str]:
    lines: List[str] = []
    for server in servers:
        name = server.server_name
        lines.append(f"{name}  adjacent_servers  {sorted(server.adjacent_server_ids)}")
        lines.append(f"{name}  adjacent_clients  {sorted(server.adjacent_user_ids)}")
        routes = ", ".join(
            f"{host_id} via {getattr(host, 'first_link_id', '?')}"
            for host_id, host in sorted(server.hosts_db.items())
        )
        lines.append(f"{name}  routes  {routes or '(none)'}")
        lines.append(f"{name}  status_log  {sorted(server.status_updates_log)}")
        received = getattr(server, "recvd_messages_asdqw", None)
        if received is not None:
            lines.append(f"{name}  raw_bytes_received  {sorted(received)}")
    for client in clients:
        name = client.client_name
        lines.append(f"{name}  known_clients  {sorted(client.connected_user_ids)}")
        lines.append(f"{name}  chat_log  {sorted(client.chat_messages_log)}")
        lines.append(f"{name}  status_log  {sorted(client.status_updates_log)}")
    return lines


def _describe_crash(exc_type, exc_value, exc_traceback) -> dict:
    host = "(unknown host)"
    where = ""
    for frame, line_number in traceback.walk_tb(exc_traceback):
        owner = frame.f_locals.get("self")
        host = getattr(owner, "server_name", getattr(owner, "client_name", host))
        if Path(frame.f_code.co_filename).name == "ChatServer.py":
            where = f"ChatServer.py:{line_number} in {frame.f_code.co_name}"
    return {"host": host, "where": where, "error": f"{exc_type.__name__}: {exc_value}"}


def run_scenario(scenario: str, server_path: Optional[Path], result_path: Path) -> None:
    """Run one scenario through the graded harness and write the result as JSON."""
    import crc_support.CRCTestManager as manager_module
    from crc_support.ChatMessageParser import MessageParser
    from crc_support.Testers.CRCTest import CRCTest

    if server_path is None:
        server_base = manager_module.CRCServer
    else:
        spec = importlib.util.spec_from_file_location("reference_chat_server", server_path)
        module = importlib.util.module_from_spec(spec)
        # The harness finds the module's log folder through sys.modules.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        server_base = module.CRCServer

    lock = threading.Lock()
    steps: Dict[int, str] = {}
    events: List[dict] = []
    crashes: List[dict] = []
    servers: list = []
    clients: list = []
    current_step = [0]

    def record(host: str, recv_data: bytes) -> None:
        try:
            parsed = MessageParser.parse_messages(recv_data)
        except Exception as error:  # the real handler is about to raise this too
            parsed = []
            with lock:
                events.append({"step": current_step[0], "host": host,
                               "text": f"UNPARSEABLE bytes={recv_data.hex()} ({error})"})
        with lock:
            for message in parsed:
                events.append({"step": current_step[0], "host": host,
                               "text": describe_message(message)})

    class RecordingServer(server_base):
        # The harness creates a log folder beside the module defining the class.
        __module__ = server_base.__module__

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with lock:
                servers.append(self)

        def handle_messages(self, io_device, recv_data):
            record(self.server_name, recv_data)
            return super().handle_messages(io_device, recv_data)

    class RecordingClient(manager_module.CRCClient):
        __module__ = manager_module.CRCClient.__module__

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with lock:
                clients.append(self)

        def handle_messages(self, recv_data):
            record(self.client_name, recv_data)
            return super().handle_messages(recv_data)

    manager_module.CRCClient = RecordingClient

    original_init = CRCTest.__init__

    def numbered_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        def numbered(name, handler):
            def run(command_args):
                with lock:
                    current_step[0] += 1
                    steps[current_step[0]] = _summarize_command(name, command_args)
                return handler(command_args)
            return run

        for name, handler in list(self.command_handlers.items()):
            if name != "WAIT":
                self.command_handlers[name] = numbered(name, handler)

    CRCTest.__init__ = numbered_init

    def record_crash(args):
        with lock:
            crashes.append(_describe_crash(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = record_crash

    manager = manager_module.CRCTestManager(CRCServerImpl=RecordingServer, catch_exceptions=True)
    _score, results = manager.run_tests({scenario: 1})
    result = results[0]

    result_path.write_text(
        json.dumps(
            {
                "passed": bool(result["passed"]),
                "errors": result["errors"] or "",
                "exception": repr(result["exception"]) if result["exception"] else "",
                "crashes": crashes,
                "trace": render_trace(steps, events, _state_lines(servers, clients)),
            }
        ),
        encoding="utf-8",
    )


def run_in_child(scenario: str, server_path: Optional[Path], result_path: Path) -> Optional[dict]:
    """Run a scenario in its own process.  Returns None if it never shut down."""
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.unlink(missing_ok=True)
    command = [sys.executable, str(Path(__file__).resolve()), "--run-one", scenario,
               "--result-file", str(result_path)]
    if server_path is not None:
        command += ["--server", str(server_path)]
    try:
        completed = subprocess.run(
            command, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=SCENARIO_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None
    if not result_path.exists():
        last_lines = completed.stderr.strip().splitlines()[-1:] or ["no error output"]
        return {"passed": False, "errors": "", "exception": last_lines[0], "crashes": [], "trace": ""}
    return json.loads(result_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def all_scenarios() -> List[str]:
    return sorted(path.stem for path in TESTCASES_DIR.glob("*.cfg"))


def scenarios_in_bundle(bundle: int) -> List[str]:
    source = (REPO_ROOT / "tests" / BUNDLE_FILES[bundle]).read_text(encoding="utf-8")
    return sorted(set(re.findall(r'_run_scenario\("([^"]+)"\)', source)))


def report_scenario(scenario: str) -> bool:
    """Run and report one scenario.  Returns True when nothing differed."""
    folder = OUTPUT_DIR / scenario
    print(f"[{scenario}]")
    result = run_in_child(scenario, None, folder / "result.json")
    if result is None:
        print(f"  did not shut down within {SCENARIO_TIMEOUT_SECONDS}s. A host is stuck: check that")
        print("  your loop notices request_terminate and that no socket call can block.")
        return False

    print(f"  harness: {'passed' if result['passed'] else 'FAILED'}")
    for line in (result["errors"] or "").strip().splitlines()[:5]:
        print(f"    {line}")
    if result["exception"]:
        print(f"    {result['exception']}")
    for crash in result["crashes"][:3]:
        where = f" at {crash['where']}" if crash["where"] else ""
        print(f"  crash: {crash['host']}{where}: {crash['error']}")

    (folder / "yours.log").write_text(result["trace"], encoding="utf-8")
    reference_path = REFERENCE_DIR / f"{scenario}.txt"
    if not reference_path.exists():
        print(f"  no reference log at {reference_path.relative_to(REPO_ROOT)}")
        return False
    reference_text = reference_path.read_text(encoding="utf-8")
    (folder / "reference.log").write_text(reference_text, encoding="utf-8")

    reference = parse_trace(reference_text)
    differences = compare_traces(reference, parse_trace(result["trace"]))
    if not differences:
        print(f"  ✓ matches reference ({len(reference.messages)} messages)")
        return True

    print(f"  ✗ {len(differences)} difference(s); earliest:")
    for line in describe_difference(differences[0], reference.steps):
        print(f"    {line}")
    print(f"  logs: trace-logs/{scenario}/yours.log and reference.log")
    return False


def write_reference_traces(only: Optional[List[str]] = None) -> int:
    if not SOLUTION.exists():
        print("solution/ChatServer.py is not here; this command is for the instructor repo.")
        return 2
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    failed = 0
    for scenario in only or all_scenarios():
        result = run_in_child(scenario, SOLUTION, OUTPUT_DIR / scenario / "result.json")
        if result is None or not result["passed"] or result["crashes"]:
            print(f"{scenario}: NOT written, the reference did not pass cleanly: {result}")
            failed += 1
            continue
        (REFERENCE_DIR / f"{scenario}.txt").write_text(result["trace"], encoding="utf-8")
        print(f"{scenario}: written")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    # Logs are UTF-8 and the verdict lines use check marks, but Windows Python
    # defaults redirected output to cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Trace a CRC scenario and compare it to a reference.")
    parser.add_argument("--list", action="store_true", help="list scenario names and exit")
    parser.add_argument("--scenario", action="append", help="run this scenario (repeatable)")
    parser.add_argument("--bundle", type=int, choices=sorted(BUNDLE_FILES), help="run one bundle's scenarios")
    parser.add_argument("--write-reference-traces", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-one", help=argparse.SUPPRESS)
    parser.add_argument("--result-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--server", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.run_one:
        run_scenario(args.run_one, args.server, args.result_file)
        return 0
    if args.write_reference_traces:
        return write_reference_traces(args.scenario)
    if args.list:
        for bundle in sorted(BUNDLE_FILES):
            print(f"Bundle {bundle}:")
            for scenario in scenarios_in_bundle(bundle):
                print(f"  {scenario}")
        return 0

    known = all_scenarios()
    selected = args.scenario or (scenarios_in_bundle(args.bundle) if args.bundle else known)
    unknown = [name for name in selected if name not in known]
    if unknown:
        print(f"Unknown scenario(s): {', '.join(unknown)}. Use --list to see them.")
        return 2

    results = [report_scenario(scenario) for scenario in selected]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
