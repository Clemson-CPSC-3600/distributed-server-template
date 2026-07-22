"""Student starter for a Clemson Relay Chat (CRC) distributed server.

Read PROTOCOL.md before implementing these methods. Keep the ``CRCServer``
class, its constructor signature, the public/handler method signatures, and the
connection-data classes unchanged; you may add private helper methods and state
as needed. The regions marked "DO NOT EDIT" (``run``, ``handle_messages``, the
``message_handlers`` dictionary, the lower ``__init__`` configuration block, and
the logging/set helpers) are wired to the provided test harness in
``crc_support/`` and must be left as-is. Everything in ``crc_support/`` is fixed
course infrastructure and must not be modified.
"""

from crc_support.ChatMessageParser import *
from socket import *
import os
import selectors
import logging

##############################################################################################################

class BaseConnectionData():
    """ ConnectionData is an abstract base class that other classes that encapsulate data associated with an
    ongoing connection will be derived from. In this application, two classes will derive from ConnectionData,
    one representing data associated with a connected server and one representing data associated with a
    connected client.

    The fundamental responsibility of classes derived from ConnectionData is to store a write buffer
    associated with a particular socket. This server will append messages to be sent to this write buffer and
    will then send the messages at a later point when it is possible to do so (i.e. the next time select() is
    called by the main loop). This functionality is defined in this base class. Other functionality will be
    defined in derived subclasses.
    """
    def __init__(self):
        self.write_buffer = b''

class ServerConnectionData(BaseConnectionData):
    """ ServerConnectionData encapsulates data associated with a connection to another server. It derives from
    BaseConnectionData which means it contains a write buffer, in addition to additional properties defined
    in this class that are specific to connections with other servers.
    """
    def __init__(self, id, server_name, server_info):
        super(ServerConnectionData, self).__init__()
        self.id = id
        self.server_name = server_name     # Stores the name of the server
        self.server_info = server_info     # Stores a human-readable description of the server
        self.first_link_id = None          # The ID of the first host on the path to this server

class ClientConnectionData(BaseConnectionData):
    """ ClientConnectionData encapsulates data associated with a connection to a client application. It
    derives from BaseConnectionData which means it contains a write buffer, in addition to additional
    properties defined in this class that are specific to connections with client applications.
    """
    def __init__(self, id, client_name, client_info):
        super(ClientConnectionData, self).__init__()
        self.id = id
        self.client_name = client_name      # Stores the name of the client
        self.client_info = client_info      # Stores a human-readable description of the client
        self.first_link_id = None           # The ID of the first host on the path to this client

##############################################################################################################

class CRCServer(object):
    def __init__(self, options, run_on_localhost=False):
        """ Initializes important values for CRCServer objects. Noteworthy variables include:

        * self.hosts_db (dictionary): this dictionary should be used to store information about all other
            servers and clients that this server knows about. The key should be the remote machine's ID and
            the value should be its corresponding ServerConnectionData or ClientConnectionData that you create
            when processing the remote machine's Registration Message.
        * self.adjacent_server_ids (list): this list should store the IDs of all adjacent servers. You can use
            this list to find the appropriate ServerConnectionData objects stored in self.hosts_db when needed
        * self.adjacent_user_ids (list): this list should store the IDs of all adjacent clients. It serves
            the same purpose as self.adjacent_server_ids except for client machines.
        * self.status_updates_log (list): the message of any status updates addressed to this server should be
            placed in this list. This is purely for the purpose of grading.
        * self.id (int): the ID of this server. It is initialized upon class instantiation.
        * self.server_name (string): the name of this server. It is initialized upon class instantiation.
        * self.server_info (string): a description of this server. It is initialized upon class instantiation.
        * self.port (int): the port this server's listening socket should listen on.
        * self.connect_to_host (string): the human-readable name of the remote server that this server should
            connect to on startup. If this is empty then this server is the first server to come online and
            does not need to connect to any other servers on startup. THIS IS ONLY USED FOR LOGGING.
        * self.connect_to_host_addr (string): the IP address of the remote server that this server should
            connect to on startup.
        * self.connect_to_port (int): the port of the remote server that this server should connect to on
            startup. If this is empty then this server is the first server to come online and does not need to
            connect to any other servers on startup.
        * self.request_terminate (bool): a flag used by the testing application to indicate whether your code
            should continue running or shutdown. You should NOT change the value of this variable in your code

        TODO: Create your selector and store it in self.sel (see comment below).

        Args:
            options (Options): an object containing various properties used to configure the server
            run_on_localhost (bool): a boolean indiciating whether this server should connected to
                applications via localhost or an actual IP address
        Returns:
            None
        """

        # TODO: Create your selector and store it in self.sel
        self.sel = None


        # The following four variables will be used to track information about the state of the network
        # -----------------------------------------------------------------------------
        # You will create an instance of ServerData or ClientData whenever receiving a registration
        # message. In addition to updating the socket's associated ConnectionData object, you should also
        # store the new ServerData or ClientData object in the hosts_db dictionary. This will allow you to
        # access information about all known hosts on the network whenever you need it. The key for a given
        # ServerData or ClientData object should be set to the id number of that Server or Client.
        self.hosts_db = {}

        # This list should contain the ids of all servers that are directly connected to this server.
        self.adjacent_server_ids = []

        # This list should contain the ids of all clients that are directly connected to this server
        self.adjacent_user_ids = []

        # Store the content of all status messages directed to this server in this list. This is purely
        # for grading purposes
        self.status_updates_log = []


        # Do not change the contents of any variables in __init__ below this line
        # -----------------------------------------------------------------------------
        self.id = options.id                            # The numeric ID of this server
        self.server_name = options.servername           # The name of this server
        self.server_info = options.info                 # Human-readable information about this server

        self.port = options.port                        # The port this server listens to for new connections
        self.connect_to_host = options.connect_to_host  # The printable name of the remote server. Don't use
                                                        # this when actually connecting to the server
        self.connect_to_host_addr = '127.0.0.1'         # Use this IP address to connect to on startup.
        self.connect_to_port = options.connect_to_port  # The port to connect to on startup. Do not connect to
                                                        # anything if this is empty/None

        self.request_terminate = False                  # A flag used by the testing application to instruct
                                                        # your code to terminate at the end of a test.

        # This dictionary contains mappings from commands to command handlers. It is used to call the
        # appropriate message handler in self.handle_messages(). You do not need to do anything with this in
        # the code you are writing for this project.
        self.message_handlers = {
            # Message handlers
            0x00:self.handle_server_registration_message,
            0x01:self.handle_status_message,
            0x80:self.handle_client_registration_message,
            0x81:self.handle_client_chat_message,
            0x82:self.handle_client_quit_message,
        }

        self.log_file = options.log_file                # The log file output will be written to
        self.logger = None                              # The logger initialized in self.init_logging()
        self.init_logging()                             # Setup and begin logging functionality

##############################################################################################################

    def run(self):
        """ This method is called to start the server. This function should NOT be called by an code you
        write. It is being called by CRCTestManager. DO NOT EDIT THIS METHOD.

        Args:
            None
        Returns:
            None
        """
        self.print_info("Launching server %s..." % self.server_name)

        # Set up the server socket that will listen for new connections
        self.setup_server_socket()

        # If we are supposed to connect to another server on startup, then do so now
        if self.connect_to_host and self.connect_to_port:
            self.connect_to_server()

        # Begin listening for connections on the server socket
        self.check_IO_devices_for_messages()

##############################################################################################################

    def setup_server_socket(self):
        """Create the listening TCP socket, bind it to ``self.port``, and register it with the selector for READ events."""
        raise NotImplementedError



    def connect_to_server(self):
        """Connect to the bootstrap server and send it an initial ServerRegistrationMessage (``last_hop_id`` of 0)."""
        raise NotImplementedError



    def check_IO_devices_for_messages(self):
        """Run the main selector loop, dispatching each ready socket until termination, then clean up."""
        raise NotImplementedError



    def cleanup(self):
        """Unregister and close every socket registered with the selector, then shut down the selector."""
        raise NotImplementedError



    def accept_new_connection(self, io_device):
        """Accept a pending connection and register it (READ|WRITE) with a BaseConnectionData placeholder."""
        raise NotImplementedError



    def handle_io_device_events(self, io_device, event_mask):
        """Read incoming bytes and flush the write buffer for one ready message-passing socket."""
        raise NotImplementedError



    def handle_messages(self, io_device, recv_data):
        """ This function is responsible for parsing the received bytes into separate messages and then
        passing each of the received messages to the appropriate message handler. Message parsing is offloaded
        to the MessageParser class. Messages are passed to the appropriate message handler using the
        self.message_handlers dictionary which associates the appropriate message handler function with each
        valid message type value.

        You do not need to make any changes to this method.

        Args:
            io_device (...):
            recv_data (...):
        Returns:
            None
        """
        messages = MessageParser.parse_messages(recv_data)

        for message in messages:
             # If we recognize the command, then process it using the assigned message handler
            if message.message_type in self.message_handlers:
                self.print_info("Received msg from Host ID #%s \"%s\"" % (message.source_id, message.bytes))
                self.message_handlers[message.message_type](io_device, message)
            else:
                raise Exception("Unrecognized command: " + message)

##############################################################################################################

    def send_message_to_host(self, destination_id, message):
        """Route ``message`` toward ``destination_id`` by appending it to the first-hop host's write buffer."""
        raise NotImplementedError



    def broadcast_message_to_servers(self, message, ignore_host_id=None):
        """Append ``message`` to the write buffer of every adjacent server except ``ignore_host_id``."""
        raise NotImplementedError



    def broadcast_message_to_adjacent_clients(self, message, ignore_host_id=None):
        """Append ``message`` to the write buffer of every adjacent client except ``ignore_host_id``."""
        raise NotImplementedError



    def send_message_to_unknown_io_device(self, io_device, message):
        """Queue ``message`` on the socket a not-yet-registered host connected over (``io_device``)."""
        raise NotImplementedError

##############################################################################################################

    def handle_server_registration_message(self, io_device, message):
        """Process a ServerRegistrationMessage: reject duplicate IDs, record routing state, seed a brand-new neighbor, and rebroadcast."""
        raise NotImplementedError

##############################################################################################################

    def handle_client_registration_message(self, io_device, message):
        """Process a ClientRegistrationMessage: reject duplicate IDs, record routing state, welcome an adjacent client, and rebroadcast."""
        raise NotImplementedError

##############################################################################################################

    def handle_status_message(self, io_device, message):
        """Log a status message addressed to this server (destination is self or 0), otherwise forward it toward its destination."""
        raise NotImplementedError

##############################################################################################################

    def handle_client_chat_message(self, io_device, message):
        """Forward a chat message toward its destination, or return an Unknown-ID status to the sender when unknown."""
        raise NotImplementedError

##############################################################################################################

    def handle_client_quit_message(self, io_device, message):
        """Broadcast a client's quit to adjacent servers and clients, then remove the client from local state."""
        raise NotImplementedError

##############################################################################################################


    # DO NOT EDIT ANY OF THE FUNCTIONS BELOW THIS LINE
    # These are helper functions to assist with logging and list management
    # ----------------------------------------------------------------------


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
        print("[%s] \t%s" % (self.server_name,msg))
        if self.logger:
            self.logger.info(msg)



    # This function takes two lists and returns the union of the lists. If an object appears in both lists,
    # it will only be in the returned union once.
    def union(self, lst1, lst2):
        final_list = list(set(lst1) | set(lst2))
        return final_list

    # This function takes two lists and returns the intersection of the lists.
    def intersect(self, lst1, lst2):
        final_list = list(set(lst1) & set(lst2))
        return final_list

    # This function takes two lists and returns the objects that are present in list1 but are NOT
    # present in list2. This function is NOT commutative
    def diff(self, list1, list2):
        return (list(set(list1) - set(list2)))
