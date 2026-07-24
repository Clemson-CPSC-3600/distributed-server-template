"""Bundle 2 (grade B): directly-connected servers, registration, and one-hop routing.

Each test drives one CRC scenario through the instructor-provided
``CRCTestManager`` harness and asserts the scenario passed. The graded contract
is entirely behavioral -- these tests never import ``src.ChatServer`` or inspect
its private state.
"""

import pytest

from crc_support.CRCTestManager import CRCTestManager


def _run_scenario(scenario):
    """Run a single CRC scenario and return its per-scenario result dict."""
    manager = CRCTestManager(catch_exceptions=True)
    _score, results = manager.run_tests({scenario: 1})
    return results[0]


@pytest.mark.bundle(2)
def test_four_connections():
    result = _run_scenario("1_2_FourConnections")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_two_servers():
    result = _run_scenario("2_1_TwoServers")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_four_servers():
    result = _run_scenario("2_2_FourServers")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_three_servers_four_clients():
    result = _run_scenario("3_3_ThreeServers_FourClients")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_message_one_hop():
    result = _run_scenario("4_2_Message_One_Hop")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_duplicate_id_server():
    result = _run_scenario("5_2_DuplicateID_Server")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_duplicate_id_client():
    result = _run_scenario("5_3_DuplicateID_Client")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_unknown_id_client():
    result = _run_scenario("5_4_UnknownID_Client")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(2)
def test_client_quit_three_servers():
    result = _run_scenario("6_2_ClientQuit_ThreeServers")
    assert result["passed"], result["errors"]
