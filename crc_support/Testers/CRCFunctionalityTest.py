import threading, os, re, time, sys, json, copy
from optparse import OptionParser
from crc_support.Testers.CRCTest import CRCTest

class CRCFunctionalityTest(CRCTest):
    
    def __init__(self, CRCServerModule, CRCClientModule, catch_exceptions):
        super().__init__(CRCServerModule, CRCClientModule, catch_exceptions)


    def check_test_results(self, test, servers, clients):        
        problems = ""

        for state in test['final_state']:
            if state in servers:
                r, p = self.check_server(servers[state], test['final_state'][state])
                if not r:
                    problems += p

            elif state in clients:
                r, p = self.check_client(clients[state], test['final_state'][state])
                if not r:
                    problems += p

        # If there were problems, then this test fails and we return them
        if problems:
            return False, problems
        else:
            return True, None


    def check_server(self, server, configuration):
        problems = ""

        if 'adjacent_users' in configuration:
            problems += self.find_problems_with_server(server.server_name, "adjacent_user_ids", server.adjacent_user_ids, configuration['adjacent_user_ids'])
        if 'adjacent_servers' in configuration:
            problems += self.find_problems_with_server(server.server_name, "adjacent_server_ids", server.adjacent_server_ids, configuration['adjacent_server_ids'])
        if 'hosts_db' in configuration:
            problems += self.find_problems_with_server(server.server_name, "hosts_db", server.hosts_db, configuration['hosts_db'])
        if 'status_updates_log' in configuration:
            problems += self.find_problems_with_server(server.server_name, "status_updates_log", server.status_updates_log, configuration['status_updates_log'])
        
        if problems:
            return False, problems
        else:
            return True, None


    def find_problems_with_server(self, servername, propertyname, server_list, configuration_list):
        problems = ""
        if len(server_list) != len(configuration_list):
            problems += "%s: Wrong number of %s (found %i, expected %i)\n" % (servername, propertyname, len(server_list), len(configuration_list))
        
        missing_from_server = self.diff(configuration_list, server_list)
        if missing_from_server:
            problems += "%s: Missing from %s: %s\n" % (servername, propertyname, str(missing_from_server))

        extra_in_server = self.diff(server_list, configuration_list)
        if extra_in_server:
            problems += "%s: Extra in %s: %s\n" % (servername, propertyname, str(extra_in_server))

        return problems

    

    def check_client(self, client, configuration):
        problems = ""

        if 'connected_user_ids' in configuration:
            problems += self.find_problems_with_client(client.client_name, "connected_user_ids", client.connected_user_ids, configuration['connected_user_ids'])
        if 'status_updates_log' in configuration:
            problems += self.find_problems_with_client(client.client_name, "status_updates_log", client.status_updates_log, configuration['status_updates_log'])
        if 'chat_messages_log' in configuration:
            problems += self.find_problems_with_client(client.client_name, "chat_messages_log", client.chat_messages_log, configuration['chat_messages_log'])

        if problems:
            return False, problems
        else:
            return True, None


    def find_problems_with_client(self, client_name, propertyname, user_list, configuration_list):
        problems = ""
        if len(user_list) != len(configuration_list):
            problems += "%s: Wrong number of %s (found %i, expected %i)\n" % (client_name, propertyname, len(user_list), len(configuration_list))
        
        missing_from_user = self.diff(configuration_list, user_list)
        if missing_from_user:
            problems += "%s: Missing from %s: %s\n" % (client_name, propertyname, str(missing_from_user))

        extra_in_user = self.diff(user_list, configuration_list)
        if extra_in_user:
            problems += "%s: Extra in %s: %s\n" % (client_name, propertyname, str(extra_in_user))

        return problems