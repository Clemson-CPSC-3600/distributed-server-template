from os import replace
from abc import ABC
from enum import Enum
from struct import pack, unpack

# Message codes
# 0x00 - Server Registration Message
# 0x01 - Status Message
# 0x02 - Server Quit Message
# 0x80 - User Registration message
# 0x81 - User Message
# 0x82 - User Quit Message
class MessageParser:
    
    @staticmethod
    def parse_messages(bytes):
        data = bytes
        messages = []
        while(len(data) > 0):           
            msg = None
            code = data[0]
            if code == 0x00:
                msg = ServerRegistrationMessage(data)
            elif code == 0x80:
                msg = ClientRegistrationMessage(data)
            elif code == 0x01:
                msg =  StatusUpdateMessage(data)
            elif code == 0x81:
                msg =  ClientChatMessage(data)
            elif code == 0x01:
                msg =  ServerQuitMessage(data)
            elif code == 0x82:
                msg =  ClientQuitMessage(data)
            
            if msg:
                messages.append(msg)
                data = data[msg.variable_message_length:]
            else:
                raise Exception("Unrecognized message type!!")
        
        return messages


# Abstract class for messages
class Message(ABC):
    pass


# #### Server Registration Message ####
# MessageType (byte = 0x00)
# SourceID (int)
# LastHopID (int)
# ServerNameLength (byte)
# ServerInfoLength (half)
# ServerNameString (variable length, UTF-8 encoding)
# ServerInfoString (variable length, UTF-8 encoding)
class ServerRegistrationMessage(Message):
    def __init__(self, bytes):
        self.message_type = 0x00
        msg = unpack("!xIIBH", bytes[:12])
        self.source_id = msg[0]
        self.last_hop_id = msg[1]
        self.server_name_length = msg[2]
        self.server_info_length = msg[3]
        self.server_name = unpack("!{0}s".format(self.server_name_length), bytes[12:12+self.server_name_length])[0].decode()
        self.server_info = unpack("!{0}s".format(self.server_info_length), bytes[12+self.server_name_length:12+self.server_name_length+self.server_info_length])[0].decode()
        self.variable_message_length = 12 + self.server_name_length + self.server_info_length
        self.bytes = bytes[:self.variable_message_length]
        
    @staticmethod
    def bytes(source_id, last_hop_id, server_name, server_info):
        return pack("!BIIBH{0}s{1}s".format(len(server_name), len(server_info)), 0x00, source_id, last_hop_id, len(server_name), len(server_info), server_name.encode(), server_info.encode())


# #### User Registrtion Message ####
# MessageType (byte = 0x80)
# SourceID (int)
# LastHopID (int)
# UserNameLength (byte)
# UserInfoLength (half)
# UserNameString (variable length, UTF-8 encoding)
# UserInfoString (variable length, UTF-8 encoding)
class ClientRegistrationMessage(Message):
    def __init__(self, bytes):
        self.message_type = 0x80
        msg = unpack("!xIIBH", bytes[:12])
        self.source_id = msg[0]
        self.last_hop_id = msg[1]
        self.client_name_length = msg[2]
        self.client_info_length = msg[3]
        self.client_name = unpack("!{0}s".format(self.client_name_length), bytes[12:12+self.client_name_length])[0].decode()
        self.client_info = unpack("!{0}s".format(self.client_info_length), bytes[12+self.client_name_length:12+self.client_name_length+self.client_info_length])[0].decode()
        self.variable_message_length = 12 + self.client_name_length + self.client_info_length
        self.bytes = bytes[:self.variable_message_length]

    @staticmethod
    def bytes(source_id, last_hop_id, client_name, client_info):
        return pack("!BIIBH{0}s{1}s".format(len(client_name), len(client_info)), 0x80, source_id, last_hop_id, len(client_name), len(client_info), client_name.encode(), client_info.encode())


# #### Status Update Message ####
# MessageType (byte = 0x01)
# SourceID (int)
# DestinationID (int)
# MessageCode (half)
# MessageLength (int)
# MessageString (variable length, UTF-8 encoding)
class StatusUpdateMessage(Message):
    def __init__(self, bytes):
        self.message_type = 0x01
        msg = unpack("!xIIHI", bytes[:15])
        self.source_id = msg[0]
        self.destination_id = msg[1]
        self.status_code = msg[2]
        self.content_length = msg[3]
        self.content = unpack("!{0}s".format(self.content_length), bytes[15:15+self.content_length])[0].decode()
        self.variable_message_length = 15 + self.content_length
        self.bytes = bytes[:self.variable_message_length]

    @staticmethod
    def bytes(source_id, destination_id, message_code, content):
        return pack("!BIIHI{0}s".format(len(content)), 0x01, source_id, destination_id, message_code, len(content), content.encode())


# #### User Chat Message ####
# MessageType (byte = 0x81)
# SourceID (int)
# DestinationID (int)
# MessageLength (int)
# MessageString (variable length, UTF-8 encoding)
class ClientChatMessage(Message):
    def __init__(self, bytes):
        self.message_type = 0x81
        msg = unpack("!xIII", bytes[:13])
        self.source_id = msg[0]
        self.destination_id = msg[1]
        self.content_length = msg[2]
        self.content = unpack("!{0}s".format(self.content_length), bytes[13:13+self.content_length])[0].decode()
        self.variable_message_length = 13 + self.content_length
        self.bytes = bytes[:self.variable_message_length]

    @staticmethod
    def bytes(source_id, destination_id, content):
        return pack("!BIII{0}s".format(len(content)), 0x81, source_id, destination_id, len(content), content.encode())


# #### Server Shutdown Message (Extra Credit) ####
# MessageType (byte = 0x02)
# SourceID (int)
# ReplacementServerID (int)
# MessageLength (int)
# MessageString (variable length, UTF-8 encoding)
class ServerQuitMessage(Message):
    def __init__(self, bytes):
        self.message_type = 0x02
        msg = unpack("!xIII", bytes[:13])
        self.source_id = msg[0]
        self.replacement_id = msg[1]
        self.content_length = msg[2]
        self.content = unpack("!{0}s".format(self.content_length), bytes[13:13+self.content_length])[0].decode()
        self.variable_message_length = 13 + self.content_length
        self.bytes = bytes[:self.variable_message_length]

    @staticmethod
    def bytes(source_id, replacement_server_id, content):
        return pack("!BIII{0}s".format(len(content)), 0x81, source_id, replacement_server_id, len(content), content.encode())


# #### User Quit Message ####
# MessageType (byte = 0x82)
# SourceID (int)
# MessageLength (int)
# MessageString (variable length, UTF-8 encoding)
class ClientQuitMessage(Message):
    def __init__(self, bytes):
        self.message_type = 0x82
        msg = unpack("!xII", bytes[:9])
        self.source_id = msg[0]
        self.content_length = msg[1]
        self.content = unpack("!{0}s".format(self.content_length), bytes[9:9+self.content_length])[0].decode()
        self.variable_message_length = 9 + self.content_length
        self.bytes = bytes[:self.variable_message_length]

    @staticmethod
    def bytes(source_id, content):
        return pack("!BII{0}s".format(len(content)), 0x82, source_id, len(content), content.encode())