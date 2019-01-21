#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import signal
import socket
import sys
import re
import json
import subprocess
import time
from pprint import pprint
from time import sleep


EXCAVATOR_TIMEOUT = 10

class ExcavatorError(Exception):
    pass

class ExcavatorApiError(ExcavatorError):
    """Exception returned by excavator."""
    def __init__(self, response):
        self.response = response
        self.error = response['error']

class ExcavatorApi:

    def __init__(self, address=("127.0.0.1", 3456)):

        self.address = address


    def worker_add(self, algo, device):
        response = self.do_excavator_command('worker.add', [algo, str(device)])
        return response['worker_id']

    def worker_free(self, worker_id):
        self.do_excavator_command('worker.free', [str(worker_id)])

    def device_speed_reset(self, device_uuid):
        self.do_excavator_command('worker.reset.device', [str(device_uuid)])

    def algo_add(self, algo):
        self.do_excavator_command('algorithm.add', [algo])

    def algo_remove(self, algo):
        self.do_excavator_command('algorithm.remove', [algo])

    def message(self, msg):
        self.do_excavator_command('message', [msg])
    
    def print_speeds(self):
        self.do_excavator_command('algorithm.print.speeds')

    def workers_reset_speeds(self, workers):
        self.do_excavator_command('workers.reset', [str(x) for x in workers])

    def subscribe(self, region, wallet, name):
        self.do_excavator_command('subscribe', [self.get_stratum(region), wallet + "." + name])

    def unsubscribe(self):
        self.do_excavator_command('unsubscribe')

    def stop(self):
        self.do_excavator_command('miner.stop')

    def quit(self):
        self.do_excavator_command('quit')

    def device_speeds(self, device):
        res = self.do_excavator_command('worker.list')
        for w in res["workers"]:
            if(w["device_id"] == device):
                out = {}
                for a in w["algorithms"]:
                    out[a["name"]] = a["speed"]
                return out
        return {}

    def state_set(self, device_uuid, algorithm, region, wallet, name):
        params = {}
        params["btc_address"] = wallet + "." + name + ":x"
        #params["btc_address"] = "34HKWdzLxWBduUfJE9JxaFhoXnfC6gmePG.test2:x"
        params["stratum_url"] = self.get_stratum(region)
        params["devices"] = [
            {
                "device_uuid": device_uuid,
                "algorithm": algorithm,
                "params": []
            }
        ]
        self.do_excavator_command('state.set', params)

    def info(self):
        res = self.do_excavator_command('info')
        return res

    def is_alive(self):
        try:
            self.message("miner.alive")
        except (socket.timeout, socket.error):
            return False
        else:
            return True

    def get_stratum(self, region):
        return 'nhmp.%s.nicehash.com:%s' % (region, 3200)

    def do_excavator_command(self, method, params = []):
        """Sends a command to excavator, returns the JSON-encoded response.

        method -- name of the command to execute
        params -- list of arguments for the command
        """

        BUF_SIZE = 1024
        command = {
            'id': 1,
            'method': method,
            'params': params
            }
        s = socket.create_connection(self.address, EXCAVATOR_TIMEOUT)
        
        s.sendall((json.dumps(command).replace('\n', '\\n') + '\n').encode())
        response = ''
        while True:
            chunk = s.recv(BUF_SIZE).decode()
            
            if '\n' in chunk:
                response += chunk[:chunk.index('\n')]
                break
            else:
                response += chunk
        s.close()

        response_data = json.loads(response)
        if "error" not in response_data or response_data['error'] is None:
            return response_data
        else:
            raise ExcavatorApiError(response_data)


if __name__ == '__main__':
    excavator = ExcavatorApi()

    excavator.subscribe("eu", "3FkaDHat56SfuJaueRo9CCUM1rCGMK2coQ", "testrig")
    excavator.algo_add("equihash")
    excavator.worker_add("equihash", 0)

    time.sleep(2)
    excavator.workers_reset_speeds([0])
    time.sleep(3)
    print(excavator.device_speeds(0))

    excavator.print_speeds()

    excavator.stop()
