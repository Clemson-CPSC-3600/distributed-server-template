import threading, os, re, time, sys, json, copy
from optparse import OptionParser
from abc import ABC, abstractmethod

class CRCTest(ABC):
    
    def __init__(self, CRCServerModule, CRCClientModule, catch_exceptions):

        self.CRCServerModule = CRCServerModule
        self.CRCClientModule = CRCClientModule
        self.catch_exceptions = catch_exceptions

        self.threads = {}
        self.servers = {}
        self.clients = {}

        ######################################################################
        # The mapping between commands and functions
        self.command_handlers = {
            # Connection Registration message handlers
            "LAUNCHSERVER":self.launch_server,
            "LAUNCHCLIENT":self.launch_client,
            "CLIENTCOMMAND":self.run_client_command,
            "SEND":self.send_message,
            "WAIT":self.wait,
            "KILL":self.kill,
        }

        ######################################################################
        # Server options
        self.server_op = OptionParser(
            version="0.1a",
            description="CPSC 3600 CRC Server application")
        self.server_op.add_option(
            "--id",
            metavar="X", type="int",
            help="The random id value for this server")
        self.server_op.add_option(
            "--servername",
            metavar="X", type="string",
            help="The name for this server")
        self.server_op.add_option(
            "--port",
            metavar="X", type="int",
            help="The port this server listens on")
        self.server_op.add_option(
            "--info",
            metavar="X", type="string",
            help="Human readable information about this server")
        self.server_op.add_option(
            "--connect_to_host",
            metavar="X", type="string",
            help="Connect to a server running on this host")  
        self.server_op.add_option(
            "--connect_to_port",
            metavar="X", type="int",
            help="Connect to a server running on port X")  
        self.server_op.add_option(
            "--log-file",
            metavar="X",
            help="store log in file X")

        ######################################################################
        # Server options
        self.message_op = OptionParser(
            version="0.1a",
            description="CPSC 3600 CRC Server application")
        self.message_op.add_option(
            "--source",
            metavar="X", type="string",
            help="The name for this server")
        self.message_op.add_option(
            "--destination",
            metavar="X", type="string",
            help="The port this server listens on")
        self.message_op.add_option(
            "--message",
            metavar="X", type="string",
            help="Human readable information about this server")
        
        ######################################################################
        # Client options
        self.client_op = OptionParser(
            version="0.1a",
            description="CPSC 3600 CRC Client application")
        self.client_op.add_option(
            "--id",
            metavar="X", type="int",
            help="The random id value for this server")
        self.client_op.add_option(
            "-S", "--serverhost", 
            metavar="X",
            help="The name of the server to connect this client to")
        self.client_op.add_option(
            "-P", "--serverport",
            metavar="X",
            help="The port to connect to on the server")
        self.client_op.add_option(
            "-U", "--username", 
            metavar="X",
            help="The requested nickname for this client")
        self.client_op.add_option(
            "-I", "--info", 
            metavar="X",
            help="Human readable information about this client")
        self.client_op.add_option(
            "--simulate",
            action="store_true",
            help="Don't request input from a user, but instead loop waiting for commands to send")
        self.client_op.add_option(
            "--verbose",
            action="store_true",
            help="be verbose (print some progress messages to stdout)")
        self.client_op.add_option(
            "--debug",
            action="store_true",
            help="print debug messages to stdout")
        self.client_op.add_option(
            "--log-file",
            metavar="X",
            help="store log in file X")


        ######################################################################
        # Client command options
        self.client_command = OptionParser(
            version="0.1a",
            description="CPSC 3600 CRC Client application")
        self.client_command.add_option(
            "--username", 
            metavar="X",
            help="The name the client who is executing this command")
        self.client_command.add_option(
            "--command", 
            metavar="X",
            help="The command to execute")
        self.client_command.add_option(
            "--args",
            nargs='*', 
            help="The arguments to pass to the command")

        super().__init__()


    def run_test(self, test):
        try:
            self.threads.clear()
            self.servers.clear()
            self.clients.clear()

            # Loop through all of the commands in this test
            for command in test['commands']:
                # Parse out the first command, which dictates what happens on this row
                instructions = command.split(" ", 1)

                # Execute the appropriate command
                result = self.command_handlers[instructions[0]](instructions[1])

                # Store the resulting object (server or client), if returned
                if type(result) is self.CRCServerModule:
                    self.servers[result.server_name] = result
                elif type(result) is self.CRCClientModule:
                    self.clients[result.client_name] = result
                
            # Wait until all of the threads have finished running
            for x in self.threads.values():
                x['thread'].join()

            passed, errors = self.check_test_results(test, self.servers, self.clients)
            return passed, errors, None

        except Exception as e:
            if self.catch_exceptions:
                for x in self.threads.values():
                    x['app'].request_terminate = True
                for x in self.threads.values():
                    x['thread'].join()
                return False, "", e
            else: 
                raise e

    ######################################################################
    def launch_servers(self, config):
        servers = {}

        # Loop through all of the commands in this test
        for command in config['commands']:
            # Parse out the first command, which dictates what happens on this row
            instructions = command.split(" ", 1)

            # Execute the appropriate command
            result = self.launch_server(instructions[1])

            servers[result.server_name] = result

        return servers


    ######################################################################
    def launch_server(self, args):
        print("*CMD.........\tStarting " + args)
        # https://stackoverflow.com/questions/16710076/python-split-a-string-respect-and-preserve-quotes
        args = re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', args)
        options, args = self.server_op.parse_args(args)
        server = self.CRCServerModule(options, run_on_localhost=True)

        x = threading.Thread(target=server.run)
        self.threads[server.server_name] = {
            'thread':x,
            'app':server
        }
        x.start()
        return server


    ######################################################################
    def send_message(self, args):
        print("*CMD.........\tSending message: " + args)
        # https://stackoverflow.com/questions/16710076/python-split-a-string-respect-and-preserve-quotes
        args = re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', args)
        options, args = self.message_op.parse_args(args)

        source = self.servers[options.source]
        desintation = options.destination
        message = options.message

        source.write_data(desintation, message)


    ######################################################################
    def launch_client(self, args):
        print("*CMD.........\tStarting " + args)
        # https://stackoverflow.com/questions/16710076/python-split-a-string-respect-and-preserve-quotes
        args = re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', args)
        options, args = self.client_op.parse_args(args)
        client = self.CRCClientModule(options, run_on_localhost=True)
        
        x = threading.Thread(target=client.run)
        self.threads[client.client_name] = {
            'thread':x,
            'app':client
        }
        x.start()
        return client


    ######################################################################
    def run_client_command(self, args):
        print("*CMD.........\tRunning client command: " + args)
        # https://stackoverflow.com/questions/16710076/python-split-a-string-respect-and-preserve-quotes
        args = re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', args)
        options, args = self.client_command.parse_args(args)

        client = self.clients[options.username]
        if options.command == "QUIT":
            if len(args) > 0:
                client.quit(args[0])
            else:
                client.quit()
        elif options.command == "MESSAGE":
            client.message_other_client(int(args[0]), args[1])


    ######################################################################
    # One arg: time to wait
    def wait(self, args):
        print("*CMD.........\tWaiting... %s" % args)
        time.sleep(float(args))


    ######################################################################
    # One arg: what to kill
    # - ALL --> kill everything
    # - name --> kill a server or a client
    def kill(self, args):
        if args == "ALL":
            for crc in self.threads.values():
                crc['app'].request_terminate = True 
                crc['thread'].join()
        elif args in self.threads:
            self.threads[args]['app'].request_terminate = True
            self.threads[args]['thread'].join()
        else:
            # Bad name for a thread to kill...
            pass

    @abstractmethod
    def check_test_results(self, test, servers, clients):        
        pass

    # Helper function to find what differences exist in two lists
    def diff(self, list1, list2):
        return (list(set(list1) - set(list2)))

    def union(self, lst1, lst2): 
        final_list = list(set(lst1) | set(lst2)) 
        return final_list

    def intersect(self, lst1, lst2): 
        final_list = list(set(lst1) & set(lst2)) 
        return final_list