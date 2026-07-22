import threading, os, re, time, sys, json, copy
from optparse import OptionParser
from crc_support.Testers.CRCTest import CRCTest
import base64

class NetworkConnectivityTest(CRCTest):
    
    def __init__(self, CRCServerModule, CRCClientModule, catch_exceptions):
        # Create a new version of the CRC Server Module which overrides the process data function
        # and does some additional logging for use in the tests
        class NewCRCServerModule(CRCServerModule):
            def __init__(self, options, run_on_localhost=False):
                super().__init__(options, run_on_localhost)
                self.sent_messages_asdqw = []
                self.recvd_messages_asdqw = []
                self.special_map = {}


            def handle_messages(self, select_key, recv_data):
                print("[" + self.server_name + "] \tReceived " + str(recv_data))
                self.recvd_messages_asdqw.append(recv_data.hex())
                res = bytes.fromhex(recv_data.hex())
                print(res)
            

            def write_data(self, server_name, message):
                print("[" + self.server_name + "] \tSending " + message + " to " + server_name)


            def connect_to_server(self):
                before_super = copy.deepcopy(list(self.sel._fd_to_key.keys()))
                super().connect_to_server()
                post_super = copy.deepcopy(list(self.sel._fd_to_key.keys()))                
                new_key = self.diff(post_super, before_super)
                if new_key:
                    self.special_map[self.connect_to_host] = new_key[0]
                    print("[" + self.server_name + "] \tConnected to another server")
                

            def accept_new_connection(self, sock):
                before_super = copy.deepcopy(list(self.sel._fd_to_key.keys()))
                super().accept_new_connection(sock)
                post_super = copy.deepcopy(list(self.sel._fd_to_key.keys()))                
                new_key = self.diff(post_super, before_super)
                if new_key:
                    self.special_map[self.connect_to_host] = new_key[0]
                    print("[" + self.server_name + "] \tAccepted a connection from another server")


            # Helper function to find what differences exist in two lists
            def diff(self, list1, list2):
                return (list(set(list1) - set(list2)))

            def union(self, lst1, lst2): 
                final_list = list(set(lst1) | set(lst2)) 
                return final_list

            def intersect(self, lst1, lst2): 
                final_list = list(set(lst1) & set(lst2)) 
                return final_list


        # Initialize with this new class
        super().__init__(NewCRCServerModule, CRCClientModule, catch_exceptions)


    def check_test_results(self, test, servers, clients):        
        problems = ""

        for state in test['final_state']:
            if state in servers:
                r, p = self.check_server(servers[state], test['final_state'][state])
                if not r:
                    problems += p

        # If there were problems, then this test fails and we return them
        if problems:
            return False, problems
        else:
            return True, None


    def check_server(self, server, configuration):
        problems = ""

        if 'sent_messages_asdqw' in configuration:
            problems += self.find_problems_with_server(server.server_name, "sent_messages_asdqw", server.sent_messages_asdqw, configuration['sent_messages_asdqw'])
        if 'recvd_messages_asdqw' in configuration:
            problems += self.find_problems_with_server(server.server_name, "recvd_messages_asdqw", server.recvd_messages_asdqw, configuration['recvd_messages_asdqw'])
        
        if problems:
            return False, problems
        else:
            return True, None


    def find_problems_with_server(self, server_name, propertyname, server_list, configuration_list):
        problems = ""
        if len(server_list) != len(configuration_list):
            problems += "%s: Wrong number of %s (found %i, expected %i)\n" % (server_name, propertyname, len(server_list), len(configuration_list))
        
        missing_from_server = self.diff(configuration_list, server_list)
        if missing_from_server:
            problems += "%s: Missing from %s: %s\n" % (server_name, propertyname, "; ".join(missing_from_server))

        extra_in_server = self.diff(server_list, configuration_list)
        if extra_in_server:
            problems += "%s: Extra in %s: %s\n" % (server_name, propertyname, "; ".join(extra_in_server))

        return problems.replace("\r\n", "\\r\\n")