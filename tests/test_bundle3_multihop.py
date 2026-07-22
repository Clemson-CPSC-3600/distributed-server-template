"""Bundle 3 (grade A): multi-hop routing over the spanning tree at scale.

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


@pytest.mark.bundle(3)
def test_eight_connections():
    result = _run_scenario("1_3_EightConnections")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(3)
def test_eleven_servers():
    result = _run_scenario("2_3_ElevenServers")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(3)
def test_eleven_servers_eight_clients():
    result = _run_scenario("3_4_ElevenServers_EightClients")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(3)
def test_message_three_hops():
    result = _run_scenario("4_3_Message_Three_Hops")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(3)
def test_welcome_status():
    result = _run_scenario("5_1_WelcomeStatus")
    assert result["passed"], result["errors"]


@pytest.mark.bundle(3)
def test_client_quit_eleven_servers():
    result = _run_scenario("6_3_ClientQuit_ElevenServers")
    assert result["passed"], result["errors"]
