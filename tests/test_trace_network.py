"""The trace tool's comparison, with no sockets involved.  Not graded."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import trace_network  # noqa: E402


def _trace(*messages, state=()):
    steps = {number: f"command {number}" for number, _, _ in messages}
    events = [{"step": s, "host": h, "text": t} for s, h, t in messages]
    return trace_network.parse_trace(trace_network.render_trace(steps, events, state))


def test_render_and_parse_round_trip():
    trace = _trace((1, "a", "ClientQuit source=1 text=''"), state=["a  routes  1 via 1"])
    assert trace.messages == [(1, "a", "ClientQuit source=1 text=''")]
    assert trace.state == ["a  routes  1 via 1"]
    assert trace.steps == {1: "command 1"}


def test_arrival_order_and_step_are_not_differences():
    reference = _trace((1, "a", "x"), (1, "a", "y"), (2, "b", "z"))
    yours = _trace((1, "a", "y"), (2, "a", "x"), (1, "b", "z"))
    assert trace_network.compare_traces(reference, yours) == []


def test_earliest_missing_message_comes_first():
    reference = _trace((1, "a", "x"), (2, "b", "y"), (3, "c", "z"))
    yours = _trace((1, "a", "x"))
    differences = trace_network.compare_traces(reference, yours)
    assert [(d.step, d.kind, d.host) for d in differences] == [(2, "missing", "b"), (3, "missing", "c")]


def test_duplicates_count():
    reference = _trace((1, "a", "x"))
    yours = _trace((1, "a", "x"), (2, "a", "x"))
    differences = trace_network.compare_traces(reference, yours)
    assert [(d.step, d.kind) for d in differences] == [(2, "extra")]


def test_state_differences_sort_after_message_differences():
    reference = _trace((1, "a", "x"), state=["s  routes  1 via 1"])
    yours = _trace(state=["s  routes  1 via 2"])
    differences = trace_network.compare_traces(reference, yours)
    assert [d.kind for d in differences] == ["missing", "state"]
    assert differences[1].expected == "s  routes  1 via 1"


def test_every_scenario_has_a_committed_reference_log():
    for scenario in trace_network.all_scenarios():
        text = (trace_network.REFERENCE_DIR / f"{scenario}.txt").read_text(encoding="utf-8")
        assert trace_network.parse_trace(text).state, scenario


def test_bundles_cover_every_scenario_exactly_once():
    listed = [s for b in trace_network.BUNDLE_FILES for s in trace_network.scenarios_in_bundle(b)]
    assert sorted(listed) == trace_network.all_scenarios()
