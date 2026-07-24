"""Instructor-provided support code for the distributed server (CRC) project.

This package holds the client, message parser, and test harness that drive the
Clemson Relay Chat scenarios. Students implement only ``src/ChatServer.py``;
everything in this package is fixed course infrastructure and must not be
edited.
"""

from .ChatMessageParser import (
    ClientChatMessage,
    ClientQuitMessage,
    ClientRegistrationMessage,
    Message,
    MessageParser,
    ServerQuitMessage,
    ServerRegistrationMessage,
    StatusUpdateMessage,
)

__all__ = [
    "ClientChatMessage",
    "ClientQuitMessage",
    "ClientRegistrationMessage",
    "Message",
    "MessageParser",
    "ServerQuitMessage",
    "ServerRegistrationMessage",
    "StatusUpdateMessage",
]
