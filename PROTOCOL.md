# Clemson Relay Chat (CRC) Protocol Specification

This document defines the required behavior of a CRC server. It is the
authoritative wire-format and routing specification; the docstrings in
`src/ChatServer.py` are only a summary. The message packing/parsing itself is
provided in `crc_support/ChatMessageParser.py` -- you build your server on top
of those message classes, you do not re-implement them.

## The network

A CRC network is a set of **servers** connected to one another, with **clients**
connected to individual servers. Every server keeps a network-wide view so that
a client on one server can message a client on any other server.

Each new server (except the very first) connects to **exactly one** existing
server when it starts up -- its bootstrap server. Because every server adds a
single edge to the server graph, the servers form an acyclic **spanning tree**.
There is exactly one path between any two hosts, so routing is just "forward
toward the destination along the tree." The tree is **static**: there is no
server-failure detection or re-routing. The only dynamic membership changes the
protocol handles are clients registering, clients quitting, and error-status
replies. (The parser also defines a Server Quit message, `0x02`. It is reserved:
this project does not use it, and nothing you build should send it.)

### Simulated network layout

The test scenarios grow one topology incrementally -- the largest runs bring up
all **11 servers and 8 clients** below, staged over time so the harness can check
the network at different sizes. Server-to-server links are solid; each client
(dashed) attaches to exactly one server. There is a single path between any two
nodes -- that spanning tree is what your routing walks, one `first_link` hop at a
time.

```mermaid
graph TD
    S1["server 1: theshire"]
    S2["server 2: rivendale"]
    S3["server 3: grey_havens"]
    S4["server 4: lothlorien"]
    S5["server 5: moria"]
    S6["server 6: edoras"]
    S7["server 7: minastirith"]
    S8["server 8: mirkwood"]
    S9["server 9: isengard"]
    S10["server 10: osgiliath"]
    S11["server 11: barad-dur"]
    S1 --- S3
    S1 --- S2
    S2 --- S5
    S2 --- S4
    S2 --- S8
    S4 --- S6
    S4 --- S7
    S6 --- S9
    S7 --- S10
    S7 --- S11
    C101(["client 101: frodobaggins"]) -.- S1
    C102(["client 102: samgamgee"]) -.- S1
    C103(["client 103: elrond"]) -.- S2
    C104(["client 104: bilbobaggins"]) -.- S3
    C107(["client 107: balin"]) -.- S5
    C105(["client 105: peregrin"]) -.- S7
    C106(["client 106: meriadoc"]) -.- S6
    C108(["client 108: sauron"]) -.- S11
```

The same layout as text:

| Server | Connects to on startup | Clients attached |
|---|---|---|
| 1 `theshire` | none (first server) | 101 `frodobaggins`, 102 `samgamgee` |
| 2 `rivendale` | 1 `theshire` | 103 `elrond` |
| 3 `grey_havens` | 1 `theshire` | 104 `bilbobaggins` |
| 4 `lothlorien` | 2 `rivendale` | none |
| 5 `moria` | 2 `rivendale` | 107 `balin` |
| 6 `edoras` | 4 `lothlorien` | 106 `meriadoc` |
| 7 `minastirith` | 4 `lothlorien` | 105 `peregrin` |
| 8 `mirkwood` | 2 `rivendale` | none |
| 9 `isengard` | 6 `edoras` | none |
| 10 `osgiliath` | 7 `minastirith` | none |
| 11 `barad-dur` | 7 `minastirith` | 108 `sauron` |

Every machine (server or client) picks a random 32-bit id on joining, so your
code must key all per-host state by id, never by position in the tree.

## Message types

Every message begins with a one-byte message type, followed by fixed header
fields, followed by any variable-length UTF-8 string fields. All integer fields
use network byte order (big endian). The `struct` format string for each
message is shown as the unambiguous definition; it matches the `bytes()` /
constructor in `crc_support/ChatMessageParser.py`.

| Code | Class | Direction |
|---|---|---|
| `0x00` | `ServerRegistrationMessage` | server ↔ server |
| `0x01` | `StatusUpdateMessage` | server → server/client |
| `0x02` | `ServerQuitMessage` | reserved; not used in this project |
| `0x80` | `ClientRegistrationMessage` | client → server, server ↔ server |
| `0x81` | `ClientChatMessage` | client ↔ client (relayed by servers) |
| `0x82` | `ClientQuitMessage` | client → server, server ↔ server/client |

### `0x00` ServerRegistrationMessage — `!BIIBH{name}s{info}s`

| Field | Type | Size |
|---|---|---:|
| message type = `0x00` | byte | 1 |
| source id | unsigned int | 4 |
| last hop id | unsigned int | 4 |
| server name length | byte | 1 |
| server info length | unsigned short | 2 |
| server name | UTF-8 | variable |
| server info | UTF-8 | variable |

Fixed header is 12 bytes. Sent by a joining server to its bootstrap server, and
re-sent (rebroadcast) by servers to teach the rest of the tree about a server.

### `0x80` ClientRegistrationMessage — `!BIIBH{name}s{info}s`

Identical layout to the server registration message (12-byte fixed header),
with `client name` / `client info` string fields. A client sends this to its
server on startup; servers also exchange it to announce clients network-wide.

### `0x01` StatusUpdateMessage — `!BIIHI{content}s`

| Field | Type | Size |
|---|---|---:|
| message type = `0x01` | byte | 1 |
| source id | unsigned int | 4 |
| destination id | unsigned int | 4 |
| status code | unsigned short | 2 |
| message length | unsigned int | 4 |
| message string | UTF-8 | variable |

Fixed header is 15 bytes. Status codes:

- `0x00` — **Welcome.** `"Welcome to the Clemson Relay Chat network [name]"`,
  sent to a newly registered adjacent client.
- `0x01` — **Unknown destination id.** `"Unknown ID [X]"`, returned to a client
  that addressed a chat message to an id nobody has registered.
- `0x02` — **Duplicate id.** Returned when a registration reuses an id already
  in the network. The string differs by registrant type: a duplicate **server**
  registration returns `"A machine has already registered with ID [X]"`; a
  duplicate **client** registration returns `"Someone has already registered
  with ID [X]"`. In both cases `[X]` is the offending id and the reply is sent
  back over the same socket with destination id `0`.

A status message whose destination id equals the receiving server's id (or `0`)
is *for* that server: append its content to `self.status_updates_log` (used for
grading). Any other destination is forwarded toward that destination.

### `0x81` ClientChatMessage — `!BIII{content}s`

| Field | Type | Size |
|---|---|---:|
| message type = `0x81` | byte | 1 |
| source id | unsigned int | 4 |
| destination id | unsigned int | 4 |
| message length | unsigned int | 4 |
| message string | UTF-8 | variable |

Fixed header is 13 bytes. Forward it toward `destination_id` if that host is
known; otherwise return a `0x01` Unknown-ID status message to the sender.

### `0x82` ClientQuitMessage — `!BII{content}s`

| Field | Type | Size |
|---|---|---:|
| message type = `0x82` | byte | 1 |
| source id | unsigned int | 4 |
| message length | unsigned int | 4 |
| message string | UTF-8 | variable |

Fixed header is 9 bytes. Broadcast the quit to adjacent servers and adjacent
clients (never back toward the departing client), then remove the client from
`self.hosts_db` and, if adjacent, from `self.adjacent_user_ids`. Ignoring a quit
for a client you have already removed is what stops the broadcast from looping
around the tree forever.

### Worked example: reading a message off the wire

You never pack these bytes by hand, because the provided message classes do it,
but being able to read them makes logs much less mysterious. Here is a client
registration with source id `101`, last hop `0`, name `frodo`, and info `hi`:

```text
80  00 00 00 65  00 00 00 00  05  00 02  66 72 6f 64 6f  68 69
```

| Bytes | Field | Value |
|---|---|---|
| `80` | message type | `0x80`, ClientRegistrationMessage |
| `00 00 00 65` | source id | 101 |
| `00 00 00 00` | last hop id | 0, so the sender is adjacent |
| `05` | client name length | 5 |
| `00 02` | client info length | 2 |
| `66 72 6f 64 6f` | client name | `frodo` |
| `68 69` | client info | `hi` |

## Registration, adjacency, and routing

### The `last_hop_id == 0` adjacency convention

A registration message's `last_hop_id` field encodes *who forwarded it*:

- `last_hop_id == 0` means an **initial registration** sent directly by the host
  that just connected to you -- so that host is **adjacent** to you.
- Otherwise the message was **relayed**, and `last_hop_id` is the id of the
  adjacent server that forwarded it to you.

When a server rebroadcasts a registration it sets `last_hop_id` to **its own
id** so the next server knows which neighbor to route back through. (A server
announcing *itself* onward therefore sends `last_hop_id == source_id`, which is
also treated as adjacent by the receiver.)

### `first_link_id` — the next hop toward a host

For every host it learns about, a server stores a `ServerConnectionData` or
`ClientConnectionData` in `self.hosts_db`, keyed by id, and records its
**`first_link_id`**: the id of the adjacent host that is the first step on the
path toward that destination.

- If the host is adjacent, `first_link_id` is the host's own id.
- If the host was learned via a relayed registration, `first_link_id` is the
  `last_hop_id` of that registration (the neighbor who told you about it).

Sending to a host is therefore a purely local decision. A server needs only its
own record of the destination's first link, and because that first link always
names an **adjacent** host (one it holds a direct socket to), the bytes leave on
the correct link. The next server makes the same decision from its own records,
so a message walks the spanning tree one hop at a time until it arrives.

### What a server does on registration

On a **server registration** (`handle_server_registration_message`):

1. If `source_id` is already in `hosts_db`, reply with a `0x02` duplicate-id
   status over the same socket (via `send_message_to_unknown_io_device`, because
   the sender's id is not yet trusted) and stop.
2. Otherwise build a `ServerConnectionData`, set its `first_link_id`, and store
   it in `hosts_db`. If adjacent, add `source_id` to `adjacent_server_ids` and
   replace the socket's selector data with that `ServerConnectionData`.
3. If this is a brand-new adjacent server (`last_hop_id == 0`), **seed** it:
   send it your own `ServerRegistrationMessage` (with `last_hop_id == self.id`)
   and a `ServerRegistrationMessage` / `ClientRegistrationMessage` for every
   other host you already know (also `last_hop_id == self.id`) so it can route
   to them through you.
4. Rebroadcast the new server's registration to your other adjacent servers,
   ignoring the neighbor you learned it from (`first_link_id`).

On a **client registration** (`handle_client_registration_message`) the shape is
the same, with two differences: a newly adjacent client is sent a `0x00` Welcome
status and told about every existing client, and the registration is broadcast
to adjacent servers **and** adjacent clients (again skipping the source).

Python's `isinstance()` tells you which kind of host you stored when you seed a
new neighbor.

### Worked example: a server joins, then a chat crosses the network

This comes from the scenario `4_2_Message_One_Hop`, and every message below is
in its reference log. `theshire` (id 1) and `rivendale` (id 2) are already
connected, and client `frodobaggins` (id 101) is on `theshire`. Now `grey_havens`
(id 3) starts and connects to `theshire`.

```mermaid
sequenceDiagram
    participant G as grey_havens (3)
    participant T as theshire (1)
    participant R as rivendale (2)
    G->>T: ServerRegistration source=3 last_hop=0
    T->>G: ServerRegistration source=1 last_hop=1
    T->>G: ServerRegistration source=2 last_hop=1
    T->>G: ClientRegistration source=101 last_hop=1
    T->>R: ServerRegistration source=3 last_hop=1
```

The same exchange as text, with what each receiver learns:

| # | Receiver | Message | What the receiver learns |
|---|---|---|---|
| 1 | `theshire` | server 3, `last_hop=0` | Server 3 is adjacent, so 3 is reached via 3. |
| 2 | `grey_havens` | server 1, `last_hop=1` | The source is the last hop, so 1 is adjacent: 1 is reached via 1. |
| 3 | `grey_havens` | server 2, `last_hop=1` | 2 is not adjacent: 2 is reached via 1. |
| 4 | `grey_havens` | client 101, `last_hop=1` | 101 is reached via 1. |
| 5 | `rivendale` | server 3, `last_hop=1` | 3 is reached via 1, the neighbor who relayed it. |

The server's first-link records are what a chat follows. Later in the scenario,
client `elrond` (id 103, on `rivendale`) messages `frodobaggins`:

| Hop | Host | Its record for 101 | What happens |
|---|---|---|---|
| 1 | `rivendale` | 101 via 1 | Forwards toward `theshire`. |
| 2 | `theshire` | 101 via 101 | 101 is adjacent, so it delivers to the client. |

Run `python tools/trace_network.py --scenario 4_2_Message_One_Hop` and open
`trace-logs/4_2_Message_One_Hop/reference.log` to see this whole scenario,
including the routes every server ends with.

## Non-blocking I/O with `selectors` (no threads)

Your server must manage every connection with a single
`selectors.DefaultSelector` event loop and **non-blocking** sockets. Do **not**
use threads; the test harness already runs each server in its own thread and
your server must cooperate with a shared shutdown flag.

### Why non-blocking

Most socket operations -- `recv()`, `accept()`, `connect()`, and in principle
`send()` -- are *blocking*: the call does not return until it can complete. With
a single connection that is fine, but a CRC server juggles many sockets at once,
and blocking on one starves the rest. Say your server is watching two peers, A
and B. If you call `A.recv()` while A has nothing to say, the whole server halts
there -- even if B has a message waiting, and even if A is itself waiting on
something B was about to send. The server deadlocks.

A **selector** fixes this. You register every socket with the selector, then ask
it which sockets are *ready* before you touch them. You only ever call
`recv`/`send` on a socket the selector just reported as readable/writable, so no
individual call blocks.

### The pattern

The example below is a complete, self-contained program that has nothing to do
with CRC: a server that sends each client's text back in upper case. It shows
how the `selectors` calls fit together. Your server will look different, because
it has more kinds of connections and its reads are handed to a message parser,
but it uses the same calls.

```python
import selectors
import socket


class Connection:
    """Whatever you want to remember about one socket."""
    def __init__(self):
        self.outbox = b""        # bytes waiting until the socket can take them


sel = selectors.DefaultSelector()

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", 9000))
listener.listen()
listener.setblocking(False)
sel.register(listener, selectors.EVENT_READ, None)   # data=None marks the listener

running = True
while running:
    # select() returns the sockets that are ready. The timeout means it wakes up
    # regularly even when nothing happens, so the loop can check `running`.
    for key, mask in sel.select(timeout=0.1):
        if key.data is None:                         # the listener: a new client
            conn, _address = key.fileobj.accept()
            conn.setblocking(False)
            sel.register(conn, selectors.EVENT_READ | selectors.EVENT_WRITE, Connection())
            continue

        sock, state = key.fileobj, key.data
        if mask & selectors.EVENT_READ:
            data = sock.recv(4096)
            if not data:                             # empty read: the peer closed
                sel.unregister(sock)
                sock.close()
                continue
            state.outbox += data.upper()
        if mask & selectors.EVENT_WRITE and state.outbox:
            sent = sock.send(state.outbox)           # may send only part of it
            state.outbox = state.outbox[sent:]

for key in list(sel.get_map().values()):             # every registered socket
    sel.unregister(key.fileobj)
    key.fileobj.close()
sel.close()
```

Two more selector calls you will want:

- `sel.modify(sock, events, new_data)` replaces the events and the data attached
  to a socket that is already registered.
- `sel.get_map()` is a mapping of everything registered. Wrap its values in
  `list()` before a loop that unregisters sockets, as above, because the mapping
  changes as you go.

Three things that trip students up:

1. `select()` almost always reports a socket as writable, so send only when there
   is something queued, and drop what you sent. Otherwise you will spin sending
   empty or duplicate bytes.
2. Without the short `timeout`, `select()` can block forever and your server can
   never notice it has been asked to shut down.
3. Scenarios restart servers on the same fixed ports seconds apart. Without
   `SO_REUSEADDR` on the listening socket, `bind()` can fail with "address already
   in use" because the previous run's port has not been released yet.

### Checklist

1. Create the selector in `__init__` (`self.sel`). Register the listening socket
   for `EVENT_READ` with a data value of `None` so you can tell it apart from
   message-passing sockets.
2. Accept new connections with a `BaseConnectionData` placeholder registered for
   `EVENT_READ | EVENT_WRITE`; you replace it with the right `ConnectionData`
   once you process that connection's registration message.
3. The main loop (`check_IO_devices_for_messages`) must call `self.sel.select`
   **inside** the loop with a short timeout (e.g. `0.1s`) so the server can
   notice `self.request_terminate` and shut down. Never make a blocking
   `select()`, `recv()`, `accept()`, or `send()` call.
4. Queue outgoing bytes in a connection's `write_buffer`; on an `EVENT_WRITE`,
   send the buffer only if it is non-empty and clear it afterward so you never
   resend the same bytes.
5. An empty `recv()` means the peer closed the connection -- unregister and close
   that socket.
6. `cleanup()` (called when the loop exits) must unregister and close every
   socket and close the selector.

## Grading-visible state

After each scenario the harness looks at your server's observable state, so keep
these accurate: `self.hosts_db` (id → connection data for every known host),
`self.adjacent_server_ids`, `self.adjacent_user_ids`, and
`self.status_updates_log`. Grading judges final state and the messages clients
receive, not the exact number or ordering of the messages your server emits, as
long as the network converges to the correct state. The grader does not require a
particular private design.
