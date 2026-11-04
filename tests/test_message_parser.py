"""Every message type must survive a round trip through the provided parser.

A duplicated opcode branch once made ServerQuitMessage unreachable, and no test
noticed because no scenario constructs one.  Not graded: no bundle marker.
"""

import pytest

from crc_support.ChatMessageParser import (
    ClientChatMessage,
    ClientQuitMessage,
    ClientRegistrationMessage,
    MessageParser,
    ServerQuitMessage,
    ServerRegistrationMessage,
    StatusUpdateMessage,
)

CASES = [
    (0x00, ServerRegistrationMessage, ServerRegistrationMessage.bytes(1, 0, "a", "b")),
    (0x01, StatusUpdateMessage, StatusUpdateMessage.bytes(1, 2, 0x00, "hello")),
    (0x02, ServerQuitMessage, ServerQuitMessage.bytes(1, 2, "bye")),
    (0x80, ClientRegistrationMessage, ClientRegistrationMessage.bytes(101, 0, "c", "d")),
    (0x81, ClientChatMessage, ClientChatMessage.bytes(101, 102, "hi")),
    (0x82, ClientQuitMessage, ClientQuitMessage.bytes(101, "bye")),
]


@pytest.mark.parametrize("code,message_class,raw", CASES)
def test_each_opcode_parses_to_its_own_class(code, message_class, raw):
    assert raw[0] == code
    messages = MessageParser.parse_messages(raw)
    assert [type(message) for message in messages] == [message_class]
    assert messages[0].message_type == code
    assert messages[0].bytes == raw


def test_several_messages_in_one_read_are_split_apart():
    raw = b"".join(case[2] for case in CASES)
    assert [type(m) for m in MessageParser.parse_messages(raw)] == [case[1] for case in CASES]
