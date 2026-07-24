"""Bundle 1 (grade C): single-server bring-up and basic client handling.

Each test drives one CRC scenario through the instructor-provided
``CRCTestManager`` harness, which launches the servers and clients as threads on
real localhost sockets and then compares their final state against the
scenario's ``.cfg``. The graded contract is entirely behavioral -- these tests
never import ``src.ChatServer`` or inspect its private state.
"""

import pytest

from crc_support.CRCTestManager import CRCTestManager


def _run_scenario(scenario):
    """Run a single CRC scenario and return its per-scenario result dict."""
    manager = CRCTestManager(catch_exceptions=True)
    _score, results = manager.run_tests({scenario: 1})
    return results[0]


@pytest.mark.bundle(1)
def test_two_connections():
    result = _run_scenario("1_1_TwoConnections")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(1)
def test_one_server_one_client():
    result = _run_scenario("3_1_OneServer_OneClient")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(1)
def test_one_server_two_clients():
    result = _run_scenario("3_2_OneServer_TwoClients")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(1)
def test_message_zero_hops():
    result = _run_scenario("4_1_Message_Zero_Hops")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(1)
def test_client_quit_one_server():
    result = _run_scenario("6_1_ClientQuit_OneServer")
    assert result["passed"], result["errors"]
