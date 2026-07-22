# Specification Grading: Distributed Server Network

This assignment uses three cumulative, pass/fail bundles. Each bundle is one
grading point. A bundle is complete only when every test assigned to it passes,
and a higher bundle is awarded only after every lower bundle is complete.

| Completed bundles | Grade level | Required behavior |
|---|---|---|
| none | Not passing | Cannot yet bring a single server online and serve a local client |
| Bundle 1 | C | Single-server bring-up and directly-connected client handling |
| Bundles 1–2 | B | Two connected servers: registration, network state, one-hop routing, error status |
| Bundles 1–3 | A | Multi-hop spanning-tree routing at scale and network-wide quit propagation |

## Bundle 1: single-server bring-up (grade C)

Bundle 1 requires:

- a non-blocking `selectors` event loop that stands up the listening socket and
  accepts new connections;
- registering a directly-connected client and sending it the `0x00` Welcome
  status;
- telling a newly connected client about the clients already on the server;
- routing a chat message between two clients on the same server (zero hops);
- handling a client quit on a single server.

Scenarios: `1_1_TwoConnections`, `3_1_OneServer_OneClient`,
`3_2_OneServer_TwoClients`, `4_1_Message_Zero_Hops`, `6_1_ClientQuit_OneServer`.

## Bundle 2: two servers and one-hop routing (grade B)

Bundle 2 requires:

- server-to-server registration between two directly-connected servers,
  including seeding a brand-new neighbor with the hosts you already know;
- maintaining correct `hosts_db`, `adjacent_server_ids`, and `adjacent_user_ids`
  as servers and clients join;
- routing status and chat messages one hop between adjacent servers;
- returning `0x02` duplicate-id status for a reused server or client id and
  `0x01` unknown-id status for a chat to an unknown destination;
- propagating a client quit across a few servers.

Scenarios: `1_2_FourConnections`, `2_1_TwoServers`, `2_2_FourServers`,
`3_3_ThreeServers_FourClients`, `4_2_Message_One_Hop`, `5_2_DuplicateID_Server`,
`5_3_DuplicateID_Client`, `5_4_UnknownID_Client`, `6_2_ClientQuit_ThreeServers`.

## Bundle 3: multi-hop routing at scale (grade A)

Bundle 3 requires:

- correct `first_link_id` routing so messages traverse multiple hops over the
  spanning tree (up to a three-hop delivery);
- building and serving a large network (up to eleven servers, eight clients);
- a welcome-status path that is correct even in the larger topology;
- propagating a client quit across the entire eleven-server network without the
  broadcast looping.

Scenarios: `1_3_EightConnections`, `2_3_ElevenServers`,
`3_4_ElevenServers_EightClients`, `4_3_Message_Three_Hops`, `5_1_WelcomeStatus`,
`6_3_ClientQuit_ElevenServers`.

There is no server-failure re-routing anywhere in this assignment: the spanning
tree is static. The only recovery behaviors graded are client quit and the
duplicate-id / unknown-id error-status replies.

## Scoring authority

The same `BundleTestRunner.compute_bundle_status` calculation drives local
output, GitHub Classroom, and Gradescope. `python run_tests.py` runs the tests
and reports the grade; GitHub Classroom invokes `github_grader.py 1|2|3`, which
reads the same cached bundle status; Gradescope invokes the instructor's
canonical copy of these tests. All three derive the grade the same way: pass
every test in Bundle 1 for a **C**, Bundles 1+2 for a **B**, and all three
bundles for an **A**. No path uses per-test point weights or exact reference
message-count snapshots.

Run `python run_tests.py` to see the current result. Use `--all` for every
failure or `-v` for full pytest tracebacks.
