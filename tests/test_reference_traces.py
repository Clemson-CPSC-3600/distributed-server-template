"""Guard the committed reference logs against drift from the solution.

Instructor repo only: it skips wherever ``solution/`` is absent, and because it
runs every scenario on real sockets it also needs CRC_CHECK_TRACES=1.  Not graded.
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import trace_network  # noqa: E402

pytestmark = pytest.mark.skipif(
    not trace_network.SOLUTION.exists() or os.environ.get("CRC_CHECK_TRACES") != "1",
    reason="needs solution/ and CRC_CHECK_TRACES=1",
)


@pytest.mark.parametrize("scenario", trace_network.all_scenarios())
def test_reference_log_matches_solution(scenario, tmp_path):
    result = trace_network.run_in_child(scenario, trace_network.SOLUTION, tmp_path / "result.json")
    assert result is not None and result["passed"], result
    committed = (trace_network.REFERENCE_DIR / f"{scenario}.txt").read_text(encoding="utf-8")
    differences = trace_network.compare_traces(
        trace_network.parse_trace(committed), trace_network.parse_trace(result["trace"])
    )
    assert differences == [], (
        "stale; regenerate with python tools/trace_network.py --write-reference-traces"
    )
