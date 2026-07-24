from optparse import OptionParser
from socket import *
import os, sys, threading
import selectors
import logging
import types
from crc_support.ChatMessageParser import *


class CRCClient(object):
    
    def __init__(self, options, run_on_localhost=False):
        self.request_terminate = False

        self.serveraddr = options.serverhost
        self.serverport = options.serverport

        if run_on_localhost:
            self.serveraddr = "127.0.0.1"

        self.id = options.id
        self.client_name = options.username
        self.info = options.info

        self.connected_user_ids = {}
        self.status_updates_log = []
        self.chat_messages_log = []


        # This dictionary contains mappings from commands to command handlers.
        # Upon receiving a command X, the appropriate command handler can be called with: self.message_handlers[X](...args)
        self.message_handlers = {
            # Message handlers
            0x01:self.handle_status_message,
            0x80:self.handle_client_registration_message,
            0x81:self.handle_client_chat_message,
            0x82:self.handle_client_quit_message,
        }


        # Options to help with debugging and logging
        self.log_file = options.log_file
        self.logger = None

        self.init_logging()


    def run(self):
        self.print_info("Launching client %s..." % (self.client_name))
        self.connect_to_server()

        # Send the registration message to the server
        self.send_message_to_server(ClientRegistrationMessage.bytes(self.id, 0, self.client_name, self.info))

        self.start_listening_to_server()
        


    ######################################################################
    # This block of functions ...
    def connect_to_server(self):
        self.sock = socket(AF_INET, SOCK_STREAM)
        self.sock.connect((self.serveraddr, int(self.serverport)))
        

    def start_listening_to_server(self):
        x = threading.Thread(target=self.listen_for_server_input)
        x.start()


    def listen_for_server_input(self):
        while not self.request_terminate:
            rcvd = self.sock.recv(2048)
            if rcvd:
                self.handle_messages(rcvd)
            else:
                self.print_info("Server has disconnected!")
                self.request_terminate = True

    # This is a function stub that will be completed in a future assignment
    def handle_messages(self, recv_data):
        messages = MessageParser.parse_messages(recv_data)

        for message in messages:
             # If we recognize the command, then process it using the assigned message handler
            if message.message_type in self.message_handlers:
                self.print_info("Received message from Host ID #%s \"%s\"" % (message.source_id, message.bytes))
                self.message_handlers[message.message_type](message)
            else:
                raise Exception("Unrecognized command: " + message)


    ######################################################################
    # This block of functions ...
    def send_message_to_server(self, message):
        self.print_info("Sending message to " + str(message))
        self.sock.send(message)

    ######################################################################
    # The remaining functions are command handlers. Each command handler is documented
    # with the functionality that must be supported

    def handle_client_registration_message(self,message):
        self.connected_user_ids[message.source_id] = message

    def handle_status_message(self, message):
        self.status_updates_log.append(message.content)

    def handle_client_chat_message(self, message):
        self.chat_messages_log.append(message.content)

    def handle_client_quit_message(self, message):
        del self.connected_user_ids[message.source_id]


    ######################################################################
    # Quit message    
    def message_other_client(self, destination_id, chat_message):
        msg = ClientChatMessage.bytes(self.id, destination_id, chat_message)
        self.send_message_to_server(msg)


    ######################################################################
    # Quit message    
    def quit(self, quit_message=''):
        msg = ClientQuitMessage.bytes(self.id, quit_message)
        self.send_message_to_server(msg)
    

    ######################################################################
    # This block of functions enables logging of info, debug, and error messages
    # Do not edit these functions. init_logging() is already called by the template code
    # You are encouraged to use print_info, print_debug, and print_error to log
    # messages useful to you in development

    def init_logging(self):
        # If we don't include a log file name, then don't log
        if not self.log_file:
            return

        # Get a reference to the logger for this program
        self.logger = logging.getLogger("IRCServer")
        __location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))

        # Create a file handler to store the log files
        fh = logging.FileHandler(os.path.join(__location__, 'Logs', '%s' % self.log_file), mode='w')

        # Set up the logging level. It defaults to INFO
        log_level = logging.INFO

        # Define a formatter that will be used to format each line in the log
        formatter = logging.Formatter(
            ("%(asctime)s - %(name)s[%(process)d] - "
             "%(levelname)s - %(message)s"))

        # Assign all of the necessary parameters
        fh.setLevel(log_level)
        fh.setFormatter(formatter)
        self.logger.setLevel(log_level)
        self.logger.addHandler(fh)

    def print_info(self, msg):
        print("[%s] \t%s" % (self.client_name,msg))
        if self.logger:
            self.logger.info(msg)