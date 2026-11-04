# Debugging a Distributed Server

When a test fails, it tells you *that* the network ended up wrong, for example
`frodobaggins: Missing from connected_user_ids: [104, 102, 103]`. It does not tell
you *where* things went wrong, and the host that reports the symptom is often not
the one with the problem.

A stepping debugger helps once you know which method to look at, but it is poor at
finding that method here: many hosts run on many threads, and stopping one at a
breakpoint makes the others' timing wrong. So the project ships a tool that shows
you the whole network at once.

## Quick start

From the project root, with your virtual environment activated:

```bash
source venv/bin/activate          # Windows: .\venv\Scripts\Activate.ps1

python tools/trace_network.py --list                           # scenario names, by bundle
python tools/trace_network.py --scenario 4_2_Message_One_Hop   # trace one scenario
python tools/trace_network.py --bundle 1                       # trace one bundle
python tools/trace_network.py                                  # all of them (a few minutes)
```

It runs your server through the same harness the graded tests use, then prints a
verdict. A healthy scenario looks like this:

```text
[4_2_Message_One_Hop]
  harness: passed
  ✓ matches reference (37 messages)
```

Here is one with a bug, where a server never forwards registrations to its
adjacent clients:

```text
[4_2_Message_One_Hop]
  harness: FAILED
    frodobaggins: Wrong number of connected_user_ids (found 0, expected 3)
    frodobaggins: Missing from connected_user_ids: [104, 102, 103]
    samgamgee: Wrong number of connected_user_ids (found 1, expected 3)
    samgamgee: Missing from connected_user_ids: [104, 103]
    elrond: Wrong number of connected_user_ids (found 2, expected 3)
  ✗ 9 difference(s); earliest:
    step 05 (start client samgamgee (id 102) on theshire): frodobaggins never received:
      ClientRegistration source=102 last_hop=1 name='samgamgee' info='"Test info"'
  logs: trace-logs/4_2_Message_One_Hop/yours.log and reference.log
```

Read it from the bottom up. The harness lines are the symptom. The "earliest"
lines are where to start: when `samgamgee` joined, `frodobaggins` should have heard
about it from `theshire`, and did not.

If your server raised an exception, you also get a line naming the host and where
it happened. Exceptions on a server thread are otherwise easy to miss:

```text
  crash: theshire at ChatServer.py:202 in setup_server_socket: NotImplementedError:
```

The `crash:` line for a client that follows, such as `ConnectionRefusedError`, is
usually a consequence. Fix the first one first.

## What it writes

| File | What it is |
|---|---|
| `trace-logs/<scenario>/yours.log` | what your network did |
| `trace-logs/<scenario>/reference.log` | the same, from a correct network |
| `Logs/<scenario>.log` | everything printed during the run, including your own logging |

Open `yours.log` and `reference.log` side by side. The reference logs ship with the
repo in `crc_support/reference_traces/`.

## How to read a log

A log is a list of **steps**, one per scenario command, followed by the final state
of every host. Under each step is what each host **received** while that step was
current:

```text
# step 04: start server grey_havens (id 3, connects to theshire)
step 04  grey_havens  ClientRegistration source=101 last_hop=1 name='frodobaggins' info='"Test info"'
step 04  grey_havens  ServerRegistration source=1 last_hop=1 name='theshire' info='"Home of the Hobbits"'
step 04  grey_havens  ServerRegistration source=2 last_hop=1 name='rivendale' info='"Elronds House"'
step 04  rivendale  ServerRegistration source=3 last_hop=1 name='grey_havens' info='"Gates to the Blessed Realm"'
step 04  theshire  ServerRegistration source=3 last_hop=0 name='grey_havens' info='"Gates to the Blessed Realm"'
```

Each line is `step`, the host that received the message, and the message's decoded
fields, as defined in [PROTOCOL.md](PROTOCOL.md). Quotation marks inside `info`
values are part of the scenario's text. A message relayed across the network shows
up once at each host it passes through, so you can follow it hop by hop.

The final state section is each host's view of the network at the end of the run:

```text
rivendale  adjacent_clients  [103]
rivendale  adjacent_servers  [1]
rivendale  routes  1 via 1, 3 via 1, 101 via 1, 102 via 1, 103 via 103, 104 via 1
rivendale  status_log  []
```

`routes` is the part no test prints directly. `3 via 1` means "my `first_link_id`
for host 3 is 1." A `?` means the object you stored for that host has no
`first_link_id`.

## Finding your bug

1. **Start with the lowest failing bundle, and its smallest scenario.** Bundle 1
   problems will show up again as confusing failures in Bundles 2 and 3.
2. **Go to the earliest difference.** Later differences are often consequences of
   it: a server that never learned about a client cannot route to that client.
3. **Find the host that should have sent the message.** The missing line tells you
   who did not receive something. Look one hop back along the route to find who
   should have sent it, then check whether that host received what it needed.
4. **Add your own logging.** Call `self.print_info(...)` in your server. The output
   appears in `Logs/<scenario>.log`, prefixed with the server's name.
5. **Fix one thing and re-run.**

## Symptom → likely cause

| What you see | Where to look |
|---|---|
| `crash:` naming `NotImplementedError` | That method is the next one to write. |
| `did not shut down within ...` | Your loop is not noticing `request_terminate`, or a socket call or `select()` is blocking. |
| A host receives nothing at all | Check the listening socket and the event loop. Is the socket registered, and do you read when a socket is readable? |
| Messages sit unsent, so the next hop never gets them | Queued bytes only leave on a write event. Is every accepted socket registered for writing? Do you send when the buffer is non-empty? |
| The same message is received over and over, or `extra` copies appear | You are not removing sent bytes from the write buffer, or a broadcast is going back toward where it came from. |
| A new server does not know hosts that joined before it | The seeding step in "What a server does on registration" in [PROTOCOL.md](PROTOCOL.md). |
| A host two or more hops away never hears about a new host | Rebroadcast. Each server has to pass a registration on to its other neighbors. |
| A `routes` entry names a host that is not adjacent, or has the wrong neighbor | `first_link_id`, and the `last_hop_id` you put on messages you relay. |
| Messages reach the wrong branch of the tree, or stop partway | You are sending toward the destination itself, not toward its first link. |
| `Address already in use` | The listening socket needs `SO_REUSEADDR`, and `cleanup()` must close every socket. |
| Clients are missing the `Welcome` status, or an error status has the wrong text | Compare the `text=` field with the status codes in [PROTOCOL.md](PROTOCOL.md). The strings are exact. |
| A quit keeps circulating, or a client remains in `hosts_db` | Ignore a quit for a client you have already removed, and remove the client from every list that holds it. |

## What a difference does and does not mean

These runs use real sockets and threads, so messages may arrive in a different
order each time. The comparison allows for that: it treats each host's messages as
a collection, so the order within a run never counts as a difference, and the step
number only says when a difference first shows up.

A difference from the reference is a strong hint, not a verdict. A correct server
could send a harmless extra message that the reference does not. **`python
run_tests.py` is the authority on whether your project passes**, and it judges the
final state, not the message log.

On a heavily loaded machine a scenario can occasionally miss one of its timing
windows even with correct code. If a failure disappears when you re-run with
nothing else running, it was timing rather than your logic.
