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
        """ This function is responsible for setting up the server socket and registering it with your 
        selector.
        
        TODO: You need to create a TCP server socket and bind it to self.port (defined in __init__). Once the 
            socket has been created, register the socket with the selector created in __init__ and start 
            listening for incoming TCP connectionn requests.
        
        NOTE: Server sockets are read from, but never written to. This is important when registering the 
            socket with your selector
        NOTE: Later on, you will need to differentiate between the server socket (which accepts new 
            connections) and all other sockets that are passing messages back and forth between hosts. You can
            use what is stored in the data parameter that you provide when registering your socket with the  
            selector to accomplish this.
        
        Args:
            None
        Returns:
            None        
        """
        raise NotImplementedError



    def connect_to_server(self):
        """ This function is responsible for connecting to a remote CRC server upon starting this server. Each
        new CRC Server (except for the first one) registers with an existing server on start up. That server 
        is its entry point into the existing CRC server network.
        
        TODO: Create a TCP socket and connect it to the remote server that exists at the following address:
            (self.connect_to_host_addr, self.connect_to_port)
        TODO: Register this socket with your selector. 
        TODO: Send a ServerRegistrationMessage to the server you just connected to. All initial server 
            registration messages MUST have their last_hop_id set to 0. Rebroadcasts of this message should 
            contain put the ID of the server that repeated the message in the last_hop_id field as normal.

        NOTE: Even though you know this is a server, it's best to use a BaseConnectionData object for the data
            parameter to be consistent with how other connections are added. That will get modified when you 
            get a registration message from the server you just connected to.

        Args:
            None
        Returns:
            None        
        """
        raise NotImplementedError



    def check_IO_devices_for_messages(self):
        """ This function manages the main loop responsible for processing input and output on all sockets 
        this server is connected to. This is accomplished in a nonblocking manner using the selector created 
        in __init__.
        
        TODO: Within the while loop, request a list of all sockets that are ready to perform a nonblocking 
            operation from the selector. Process those events based on the event type and the type of socket 
            (either a listening socket, a socket connected to a remote server, a socket connected to a remote 
            client, or a socket connected to an application whose type is not known yet).
        TODO: When processing READ events for this server's listening socket, call 
            self.accept_new_connection(io_device).
        TODO: When processing events associated with any other socket, call 
            self.handle_io_device_events(io_device, event_mask).
        TODO: Call self.cleanup() once the while loop terminates (i.e. the program needs to shut down)

        NOTE: All calls to select() MUST be inside the while loop. Select() is itself a blocking call and we 
            need to be able to terminate the server to test its functionality. The server may not be able to  
            shut down if calls to select() are made outside of the loop since those calls can block.
        NOTE: Pass a short timeout value into your select() call (e.g. 0.1 seconds) to prevent your code from 
            hanging when it is time to terminate it

        Args:
            None
        Returns:
            None        
        """
        raise NotImplementedError



    def cleanup(self):
        """ This function handles releasing all allocated resources associated with this server (i.e. our 
        selector and any sockets opened by this server).
        
        TODO: Shut down your listening socket
        TODO: Shut down and unregister all sockets registered with the selector
        TODO: Shut down the selector

        NOTE: You can get a list of all sockets registered with the selector by accessing a hidden dictionary 
            of the selector: _fd_to_keys. You can extract a list of io_devices from this using the command: 
            list(self.sel._fd_to_key.values())

        Args:
            None
        Returns:
            None        
        """
        raise NotImplementedError



    def accept_new_connection(self, io_device):
        """ This function is responsible for handling new connection requests from other servers and from 
        clients. This function should be called from self.check_IO_devices_for_messages whenever the listening 
        socket has data that can be read.
        
        TODO: Accept the connection request and register it with your selector. All sockets registered here  
            should be registered for both READ and WRITE events. 

        NOTE: You don't know at this point whether new connection requests are comming from a new server or a  
            new client (you'll find that out when processing the registration message sent over the connected  
            socket). As such you don't know whether to use a ServerConncetionData or a ClientConnectionData  
            object when registering the socket with the selector. Instead, use a BaseConnectionData object so 
            you have access to a write_buffer. We'll replace this with the appropriate object later when 
            handling the registration message.

        Args:
            io_device (...): 
        Returns:
            None        
        """
        raise NotImplementedError



    def handle_io_device_events(self, io_device, event_mask):
        """ This function is responsible for handling READ and WRITE events for a given IO device. Incomming  
        messages will be read and passed to the appropriate message handler here and the write buffer  
        associated with this socket will be sent over the socket here.
        
        TODO: Check to see if this is a READ event and/or a WRITE event (it's possible to be both). 
        
        TODO: If this is a read event, read the bytes and pass the read bytes to self.handle_messages() along 
            with the io_device containing this socket and its associated data object. If no bytes are returned
            by the call to read() then the machine on the other side of the socket has closed their side of 
            the connection. You should unregister and close this socket if that happens.
        
        TODO: If this is a write event, check the write_buffer stored in the current io_device's associated 
            data object. If the write_buffer contains any data go ahead and write it to the socket and clear 
            the write buffer. You must check to see if the write_buffer contains any data as you don't want to
            send any empty messages. Similarly, you must clear the write buffer after sending because you 
            don't want to send any duplicate messages

        Args:
            io_device (...):
            event_mask (...): 
        Returns:
            None        
        """
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
        """ This is a helper function meant to encapsulate the code needed to send a message to a specific 
        host machine, identified based on the machine's unique ID number.

        TODO: Append the message to the appropriate write_buffer that will get this message (eventually) to 
            its intended destination.

        Args:
            destination_id (int): the ID of the destination machine
            message (bytes): the packed message to be delivered 
        Returns:
            None        
        """
        raise NotImplementedError



    def broadcast_message_to_servers(self, message, ignore_host_id=None):
        """ This is a helper function meant to encapsulate the code needed to broadcast a message to the 
        entire network of connected servers. You may call self.send_message_to_host() in this function. 
        Remember that it is only possible to send messages to servers that are adjacent to you. Those adjacent 
        servers can then continue to broadcast this message to futher away machines after they receive it. 
        This function should NOT broadcast messages to adjacent clients.

        You will sometimes need to exclude a host from the broadcast (e.g. you don't want to broadcast a 
        message back to the machine that originally sent you this message). You should use the ignore_host_id 
        parameter to help with this. If the ID is None than the message should be broadcast to all adjacent 
        servers. Alternatively, if it is not None you should not broadcast the message to any adjacent 
        servers with the ID value included in the parameter.

        TODO: Append the message to the appropriate write_buffers needed to broadcast this message to all 
            adjacent servers except for servers with IDs equal to the value in ignore_host_id.

        Args:
            message (bytes): the packed message to be delivered
            ignore_host_id (int): the ID of a host that this message should not be delievered to 
        Returns:
            None        
        """
        raise NotImplementedError



    def broadcast_message_to_adjacent_clients(self, message, ignore_host_id=None):
        """ This is a helper function meant to encapsulate the code needed to broadcast a message to all 
        adjacent clients. Its functionality is similar to self.broadcast_message_to_servers(). The two 
        functions are separated from each other as some messages should only be broadcast to other servers, 
        not both servers and clients. If you need to broadcast a message to both servers and clients than you 
        can simply call both functions. The ignore_host_id parameter serves the same purpose as the parameter
        with the same name in the self.broadcast_message_to_servers() function.

        TODO: Append the message to the appropriate write_buffers needed to broadcast this message to all 
            adjacent clients except for machines with IDs equal to the value in ignore_host_id.

        Args:
            message (bytes): the packed message to be delivered
            ignore_host_id (int): the ID of a host that this message should not be delievered to 
        Returns:
            None        
        """
        raise NotImplementedError



    def send_message_to_unknown_io_device(self, io_device, message):
        """ The ID of a machine becomes known once it successfully registers with the network. In the event 
        that an error occurs during registration, status messages cannot be sent to the new machine based on
        the machine's ID as this isn't yet known. In this case status messages must be sent based on the 
        socket the registration message was received over. This method should encapsulate the code needed to 
        send a message across the io_device that was provided to the registration message handler when the 
        error occured.

        TODO: Append the message to the appropriate write_buffer that will get this message (eventually) to 
            its intended destination.

        Args:
            io_device (SelectorKey): An object containing a reference to the socket that the message should be
                delievered to (see io_device.fileobj).
            message (bytes): the packed message to be delievered 
        Returns:
            None        
        """
        raise NotImplementedError

##############################################################################################################

    def handle_server_registration_message(self, io_device, message):
        """ This function handles the initial registration process for new servers. 

        Upon receiving a server registration message, check to make sure a machine with that ID does not 
        already exist in the network. If the ID does already exist, send a StatusUpdateMessage with the 
        message code 0x02 and the message string "A machine has already registered with ID [X]", where [X] is 
        replaced with the ID in the registration message, and then return from this function. Since you don't 
        know the ID of the new server, use a destination_ID of 0 when making this StatusUpdateMessage.

        If the new server's ID is unique, create a new ServerConnectionData object containing the server's 
        information (e.g. server name, server_info) and store the ServerConnectionData object in 
        self.hosts_db. You will need to determine what the first_link_id value should be when creating the 
        ServerConnectionData object.

        You will need to determine if this new server is adjacent to the server processing this message. If 
        the new server is adjacent, add its ID to self.adjacent_server_ids and modify the assocated io_device 
        to replace the associated data object with your new ServerConnectionData object. You can do this by
        calling: self.sel.modify(io_device.fileobj, 
                                 selectors.EVENT_READ | selectors.EVENT_WRITE, 
                                 my_new_server_connection_data_obj)

        If this registration message came from a brand new adjacent server then it is the responsibility of 
        the server processing this message to inform the new server of all other connected servers and 
        clients. You can accomplish this by creating ServerRegistationMessages and ClientRegistrationMessages 
        for each of the existing servers and clients and sending them to the brand new adjacent machine. These
        messages will then be processed by the new adjacent server's handle_server_registration_message() 
        function. You can check if a host stored in self.hosts_db is a Server or a Client using python's 
        isinstance() command (e.g. isinstance(self.hosts_db[0], ServerConnectionData) returns True or False 
        depending on the type of the object stored in self.hosts_db[0])

        Finally, a message should be broadcast to the rest of the network informing it about this new server. 
        This message should not be broadcast back to the new machine.

        Args:
            io_device (SelectorKey): This object contains references to the socket (io_device.fileobj) and to 
                the data associated with the socket on registering with the selector (io_device.data).
            message (ServerRegistrationMessage): The new server registration message to be processed
        Returns:
            None        
        """
        raise NotImplementedError

##############################################################################################################

    def handle_client_registration_message(self, io_device, message):
        """ This function handles the initial registration process for new clients. 

        Upon receiving a client registration message, check to make sure a machine with that ID does not 
        already exist in the network. If the ID does already exist, send a StatusUpdateMessage with the 
        message code 0x02 and the message string "A machine has already registered with ID [X]", where [X] is 
        replaced with the ID in the registration message, and then return from this function. Since you don't 
        know the ID of the new client, use a destination_ID of 0 when making this StatusUpdateMessage.

        If the new client's ID is unique, create a new ClientConnectionData object containing the client's 
        information (e.g. client name, client info) and store the ClientConnectionData object in
        self.hosts_db. You will need to determine what the first_link_id value should be when creating the 
        ClientConnectionData object.

        You will need to determine if this new client is adjacent to the server processing this message. If 
        the new client is adjacent, add its ID to self.adjacent_user_ids and modify the assocated io_device 
        to replace the associated data object with your new ClientConnectionData object. You can do this by
        calling: self.sel.modify(io_device.fileobj, 
                                 selectors.EVENT_READ | selectors.EVENT_WRITE, 
                                 my_new_client_connection_data_obj)
        You should also send a Welcome status update to the newly connected adjacent client. The message code 
        should be 0x00 and the message content should be "Welcome to the Clemson Relay Chat network [X]", 
        where [X] is the client's name. 

        If this registration message came from a brand new adjacent client then it is the responsibility of 
        the server processing this message to inform the new client of all other connected clients. You can 
        accomplish this by creating ClientRegistrationMessages for each of the existing clients and sending 
        them to the brand new adjacent machine. You can check if a host stored in self.hosts_db is a Server 
        or a Client using python's isinstance() command 
        (e.g. isinstance(self.hosts_db[0], ServerConnectionData) returns True or False depending on the type 
        of the object stored in self.hosts_db[0])

        Finally, a message should be broadcast to the rest of the network informing it about this new client. 
        This message should not be broadcast back to the new machine.

        Args:
            io_device (SelectorKey): This object contains references to the socket (io_device.fileobj) and to 
                the data associated with the socket on registering with the selector (io_device.data).
            message (ClientRegistrationMessage): The new client registration message to be processed
        Returns:
            None        
        """
        raise NotImplementedError

##############################################################################################################

    def handle_status_message(self, io_device, message):
        """ This function handles status messages. 

        Upon receiving a stauts message, check is if the destination ID is your ID or if it is 0. If either of
        these are true, then the status message is addressed to you. Append the content of the message to 
        self.status_updates_log (this is just for grading purposes). Otherwise, if it is not addressed to you
        and a machine with the desintation_id of the status message exists then forward it on to its intended 
        destination. This should be a very short function.

        Args:
            io_device (SelectorKey): This object contains references to the socket (io_device.fileobj) and to 
                the data associated with the socket on registering with the selector (io_device.data).
            message (StatusUpdateMessage): The status update message that needs to be processed
        Returns:
            None        
        """
        raise NotImplementedError

##############################################################################################################

    def handle_client_chat_message(self, io_device, message):
        """ This function handles client chat messages. 

        Upon receiving a chat message, check to see if the intended destination_id exists. If so, forward the 
        chat message on to the intended destination. If the intended destination_id does not exist then send
        a StatusUpdateMessage back to the machine that sent you this chat message with an UnknownID message 
        code of 0x01 with the message content "Unknown ID [X]", where [X] is replaced with the Unknown ID. 
        This should be a short function.
       
        Args:
            io_device (SelectorKey): This object contains references to the socket (io_device.fileobj) and to 
                the data associated with the socket on registering with the selector (io_device.data).
            message (ClientChatMessage): The client chat message that needs to be processed
        Returns:
            None        
        """
        raise NotImplementedError

##############################################################################################################

    def handle_client_quit_message(self, io_device, message):
        """ This function handles when a client quits the network. 

        Upon receiving a client quit message, check to make sure a client with this ID exists. If so, 
        broadcast the quit message to all over adjacent servers and clients that this client is quitting. Make 
        sure you don't send the message back to the client that is quitting. You should then delete the client
        and its ClientConnectionData from self.hosts_db and (if it is adjacent to this server) from the 
        adjacent_user_ids list.
               
        Args:
            io_device (SelectorKey): This object contains references to the socket (io_device.fileobj) and to 
                the data associated with the socket on registering with the selector (io_device.data).
            message (ClientQuitMessage): The client quit message that needs to be processed
        Returns:
            None        
        """
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
