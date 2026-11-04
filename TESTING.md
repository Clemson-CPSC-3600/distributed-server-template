# Testing the Distributed Server Network

Run all commands from the repository root and through the project-local virtual
environment.

On macOS or Linux:

```bash
source venv/bin/activate
python run_tests.py
```

In Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python run_tests.py
```

`run_tests.py` is the grading authority. It selects only tests carrying an
explicit `@pytest.mark.bundle(1|2|3)` marker, records the development snapshot in
student repositories, and reports the lowest incomplete bundle first.

## Useful commands

```bash
python run_tests.py --bundle 1          # single-server bring-up + client handling
python run_tests.py --bundle 2          # two servers: registration + one-hop routing
python run_tests.py --bundle 3          # multi-hop routing at scale
python run_tests.py --all               # show failures in every bundle
python run_tests.py -v                  # full pytest names and tracebacks
python run_tests.py -k client_quit      # pass a pytest name filter through
python run_tests.py --failed            # rerun pytest's previous failures
```

These scenarios launch several servers and clients as threads on real localhost
sockets. With a working server, the whole suite takes well under a minute, and you
can run all of it at once. A server that never stops sending, or never sends at
all, takes longer because the harness gives the network time to finish before
giving up.

## How the harness works

Each graded test drives **one** CRC scenario through the instructor-provided
`crc_support.CRCTestManager`:

```python
from crc_support.CRCTestManager import CRCTestManager

manager = CRCTestManager(catch_exceptions=True)
_score, results = manager.run_tests({"1_1_TwoConnections": 1})
assert results[0]["passed"], results[0]["errors"]
```

- A scenario is a `.cfg` file in `crc_support/TestCases/`. It lists commands
  (`LAUNCHSERVER`, `LAUNCHCLIENT`, `CLIENTCOMMAND`, `WAIT`, `KILL`) and a
  `final_state` block describing the network state every server and client must
  reach.
- `CRCTestManager` starts each server/client (`from src.ChatServer import
  CRCServer`, `from crc_support.ChatClient import CRCClient`) on its own thread
  using real localhost sockets, runs the commands, then compares the observed
  final state against the `.cfg`. `results[0]["passed"]` is the scenario's
  pass/fail and `results[0]["errors"]` describes any mismatch.
- Each run also writes a transcript of everything printed to `Logs/<scenario>.log`
  at the repo root, including your own `self.print_info(...)` calls. (`Logs/` is
  git-ignored.)

- `WAIT` does not sleep for a fixed time. It waits until the network is quiet: every
  host waiting for input, nothing queued in a `write_buffer`, and every byte sent
  already received. A correct network needs a fraction of a second, even on a busy
  machine.
- While tests run, `selectors.DefaultSelector` is the harness's own selector, which
  reports a socket as writable only when its connection has bytes in `write_buffer`.
  It keeps idle hosts asleep, so many hosts can share one machine. Your code does
  not change, but if you print inside your loop you will see fewer write events than
  [PROTOCOL.md](PROTOCOL.md) describes. Outside the tests, a socket is reported as
  writable almost all the time, so still check the buffer before you send.

You never edit `crc_support/`. The graded tests only ever call through
`CRCTestManager`; they do not import your server or inspect its private state.

## Bundle-to-scenario map

| Bundle | Test file | Scenarios |
|---|---|---|
| 1 (C) | `tests/test_bundle1_core.py` | `1_1_TwoConnections`, `3_1_OneServer_OneClient`, `3_2_OneServer_TwoClients`, `4_1_Message_Zero_Hops`, `6_1_ClientQuit_OneServer` |
| 2 (B) | `tests/test_bundle2_routing.py` | `1_2_FourConnections`, `2_1_TwoServers`, `2_2_FourServers`, `3_3_ThreeServers_FourClients`, `4_2_Message_One_Hop`, `5_2_DuplicateID_Server`, `5_3_DuplicateID_Client`, `5_4_UnknownID_Client`, `6_2_ClientQuit_ThreeServers` |
| 3 (A) | `tests/test_bundle3_multihop.py` | `1_3_EightConnections`, `2_3_ElevenServers`, `3_4_ElevenServers_EightClients`, `4_3_Message_Three_Hops`, `5_1_WelcomeStatus`, `6_3_ClientQuit_ElevenServers` |

The component order and dependencies are declared in
`project-template-config.json`; the runner uses them only to focus feedback and
suppress cascading failures. Bundle credit still requires every marked test in
the bundle to pass.

## Reading failures

Start with the lowest incomplete bundle. A failing `_run_scenario` prints the
harness's diagnostic (for example `theshire: Wrong number of hosts_db (found 0,
expected 1)`) as the assertion message. For a focused traceback, copy the
command the runner prints beneath the failure, for example:

```bash
python -m pytest "tests/test_bundle2_routing.py::test_message_one_hop" -v
```

Then trace that scenario to see which message your server dropped or misrouted:

```bash
python tools/trace_network.py --scenario 4_2_Message_One_Hop
```

[DEBUGGING.md](DEBUGGING.md) explains how to read the result.
