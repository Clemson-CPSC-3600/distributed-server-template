# Project 3: Distributed Server Network

In this project you build one server in **Clemson Relay Chat (CRC)**, a
distributed chat network. Many CRC servers connect to one another and relay
registration, chat, status, and quit messages so that any client connected to
any server can exchange messages with any other client on the network. Your
server has to bring itself online, learn the rest of the network as servers and
clients register, and route messages toward their destination over a
self-learned spanning tree -- all with non-blocking `selectors`-based I/O and no
threads.

Read [PROTOCOL.md](PROTOCOL.md) before writing code. It is the authoritative
wire-format and routing specification.

## What you implement

You work in one file:

```text
src/ChatServer.py
```

The starter declares the required `CRCServer` interface and the connection-data
classes (`BaseConnectionData`, `ServerConnectionData`, `ClientConnectionData`).
You fill in the methods that are left as `raise NotImplementedError`:

- **Socket / event loop:** `setup_server_socket`, `connect_to_server`,
  `check_IO_devices_for_messages`, `cleanup`, `accept_new_connection`,
  `handle_io_device_events`.
- **Routing helpers:** `send_message_to_host`, `broadcast_message_to_servers`,
  `broadcast_message_to_adjacent_clients`, `send_message_to_unknown_io_device`.
- **Message handlers:** `handle_server_registration_message`,
  `handle_client_registration_message`, `handle_status_message`,
  `handle_client_chat_message`, `handle_client_quit_message`.

Do not rename the class, its constructor, or these public methods, and do not
edit the regions marked "DO NOT EDIT" (`run`, `handle_messages`, the
`message_handlers` dictionary, the lower `__init__` configuration block, and the
logging/set helpers). You may add private helper methods and state as needed.
Everything under `crc_support/` -- the client, message parser, and test harness
-- is provided course infrastructure and must not be modified.

## Specification-grading bundles

| Bundle | Server capability | Grade when cumulative |
|---|---|---|
| 1 | Single-server bring-up: listening socket, selector loop, accept connections, and register/serve directly-connected clients (registration, welcome, chat to a local client, client quit) | C |
| 2 | Two directly-connected servers: server registration, network-wide state, one-hop chat routing, duplicate-ID and unknown-ID error status, client quit across a few servers | B |
| 3 | Multi-hop routing over the spanning tree at scale (up to eleven servers), three-hop delivery, and client-quit propagation across the whole network | A |

Every test in a bundle must pass, and higher bundles require all lower bundles.
Tests judge observable protocol behavior (final network state), not the exact
number or timing of the messages your server sends.

## Setup

On macOS or Linux, from the project root:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

In Windows PowerShell:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The distributed repository already contains the starter in `src/ChatServer.py`.
The copy under `template/` is a clean backup in case you need to restore it.

After the environment exists, run Python only through that local environment.
With the environment activated, `python` points to the correct interpreter; you
can also invoke `./venv/bin/python` explicitly on macOS/Linux.

## Run the tests

```bash
python run_tests.py
python run_tests.py --bundle 1
python run_tests.py --all
python run_tests.py -v
```

The normal runner executes only tests carrying an explicit Bundle 1, 2, or 3
marker. It preserves the repository's development-trace capture and reports the
lowest incomplete bundle first. See [TESTING.md](TESTING.md) for the full
bundle-to-scenario map and focused commands.

## Suggested implementation order

1. Stand up the listening socket and the selector event loop; accept incoming
   connections (Bundle 1 connection scenarios).
2. Handle a directly-connected client's registration, send the welcome status,
   and route a chat message to a local client; handle client quit.
3. Handle server registration between two adjacent servers: record routing
   state, seed the new neighbor with everything you already know, and rebroadcast.
4. Route status and chat messages one hop, and return duplicate-ID / unknown-ID
   error status messages.
5. Generalize routing so messages traverse multiple hops over the spanning tree,
   and propagate client quits across the whole network.

## Submission

Commit and push your work to the GitHub Classroom repository. The Classroom
workflow runs the same bundle authority as `python run_tests.py`; Gradescope
also uses the instructor's canonical copy of these tests. See
[GRADING.md](GRADING.md) for the scoring model.

Local AI-agent interactions and test-run snapshots are captured as described in
[AI_POLICY.md](AI_POLICY.md) and [PROCESS_TRACKING.md](PROCESS_TRACKING.md).
