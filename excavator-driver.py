#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cross-platform controller for NiceHash Excavator for Nvidia."""

# Example usage:
#   $ excavator -p 3456 &
#   $ python3 excavator-driver.py

# History:
#   2017-12-03: initial version
#   2018-01-25: group devices by common algorithm; wait for excavator on startup

__author__ = "Ryan Young"
__email__ = "rayoung@utexas.edu"
__license__ = "public domain"

import json
import logging
import signal
import socket
import sys
import json
import urllib.error
import urllib.request
from pprint import pprint
from time import sleep

WALLET_ADDR = '3FkaDHat56SfuJaueRo9CCUM1rCGMK2coQ'
WORKER_NAME = 'worker-01'
REGION = 'eu' # eu, usa, hk, jp, in, br
DEVICES = [1,2]

EXCAVATOR_ADDRESS = ('127.0.0.1', 3456)

# copy the numbers from excavator-benchmark (test one device at a time with -d <n>)
# convert to the base unit, H/s
#   x H/s   ->  x
#   x kH/s  ->  x*1e3
#   x MH/s  ->  x*1e6
#   x GH/s  ->  x*1e9
#BENCHMARKS = {}
# device 0: GTX 1060 6GB
#BENCHMARKS[0] = {
#    'equihash': 325.964731,
#    'pascal': 687.796633e6,
#    'decred': 1.896621e9,
#    'sia': 1.205557e9,
#    'lbry': 185.736261e6,
#    'blake2s': 2.767859e9,
#    'lyra2rev2': 26.157357e6,
#    'cryptonight': 443.131955,
#    'daggerhashimoto': 19.965252e6,
#    'daggerhashimoto_pascal': [8.847941e6, 495.485485e6],
#    'daggerhashimoto_decred': [19.843944e6, 714.382018e6],
#    'daggerhashimoto_sia': [19.908869e6, 254.833522e6],
#    # test manually
#    'neoscrypt': 732.554438e3,
#    'nist5': 32.031877e6
#    }

# Load benchmarks

# TODO Check if file exists
data = json.load(open('benchmark.json'))

BENCHMARKS = {}
for d in DEVICES:
    BENCHMARKS[d] = data

PROFIT_SWITCH_THRESHOLD = 0.02
UPDATE_INTERVAL = 60

EXCAVATOR_TIMEOUT = 10
NICEHASH_TIMEOUT = 20

### here be dragons

class ExcavatorError(Exception):
    pass

class ExcavatorAPIError(ExcavatorError):
    """Exception returned by excavator."""
    def __init__(self, response):
        self.response = response
        self.error = response['error']

def nicehash_multialgo_info():
    """Retrieves pay rates and connection ports for every algorithm from the NiceHash API."""
    response = urllib.request.urlopen('https://api.nicehash.com/api?method=simplemultialgo.info',
                                      None, NICEHASH_TIMEOUT)
    query = json.loads(response.read().decode('ascii')) #json.load(response)
    paying = {}
    ports = {}
    for algorithm in query['result']['simplemultialgo']:
        name = algorithm['name']
        paying[name] = float(algorithm['paying'])
        ports[name] = int(algorithm['port'])
    return paying, ports

def nicehash_mbtc_per_day(device, paying):
    """Calculates the BTC/day amount for every algorithm.

    device -- excavator device id for benchmarks
    paying -- algorithm pay information from NiceHash
    """

    benchmarks = BENCHMARKS[device]
    pay = lambda algo, speed: paying[algo]*speed*(24*60*60)*1e-11
    def pay_benched(algo):
        if '_' in algo:
            return sum([pay(multi_algo, benchmarks[algo][i]) for
                        i, multi_algo in enumerate(algo.split('_'))])
        else:
            return pay(algo, benchmarks[algo])

    return dict([(algo, pay_benched(algo)) for algo in benchmarks.keys()])

def do_excavator_command(method, params):
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
    s = socket.create_connection(EXCAVATOR_ADDRESS, EXCAVATOR_TIMEOUT)
    # send newline-terminated command
    s.sendall((json.dumps(command).replace('\n', '\\n') + '\n').encode())
    response = ''
    while True:
        chunk = s.recv(BUF_SIZE).decode()
        # excavator responses are newline-terminated too
        if '\n' in chunk:
            response += chunk[:chunk.index('\n')]
            break
        else:
            response += chunk
    s.close()

    response_data = json.loads(response)
    if response_data['error'] is None:
        return response_data
    else:
        raise ExcavatorAPIError(response_data)

def excavator_algorithm_params(algo, ports):
    """Return the required list of parameters to add an algorithm to excavator.

    algo -- the algorithm to run
    ports -- algorithm port information from NiceHash
    """

    AUTH = '%s.%s:x' % (WALLET_ADDR, WORKER_NAME)
    stratum = lambda algo: '%s.%s.nicehash.com:%s' % (algo, REGION, ports[algo])
    return [algo] + sum([[stratum(multi_algo), AUTH] for multi_algo in
                        algo.split('_')], [])

def main():
    """Main program."""
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s',
                        level=logging.INFO)

    # dict of algorithm name -> (excavator id, [attached devices])
    algorithm_status = {}
    # dict of device id -> excavator worker id
    worker_status = {}

    device_algorithm = lambda device: [a for a in algorithm_status.keys() if
                                       device in algorithm_status[a][1]][0]

    def dispatch_device(device, algo, ports):
        if algo in algorithm_status:
            algo_id = algorithm_status[algo][0]
            algorithm_status[algo][1].append(device)
        else:
            response = do_excavator_command('algorithm.add',
                                            excavator_algorithm_params(algo, ports))
            algo_id = response['algorithm_id']
            algorithm_status[algo] = (algo_id, [device])

        response = do_excavator_command('worker.add', [str(algo_id), str(device)])
        worker_status[device] = response['worker_id']

    def free_device(device):
        algo = device_algorithm(device)
        algorithm_status[algo][1].remove(device)
        worker_id = worker_status[device]
        worker_status.pop(device)

        do_excavator_command('worker.free', [str(worker_id)])

        if len(algorithm_status[algo][1]) == 0: # no more devices attached
            algo_id = algorithm_status[algo][0]
            algorithm_status.pop(algo)

            do_excavator_command('algorithm.remove', [str(algo_id)])

    def sigint_handler(signum, frame):
        logging.info('cleaning up!')

        active_devices = list(worker_status.keys())
        for device in active_devices:
            free_device(device)
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    def contact_excavator():
        try:
            do_excavator_command('message', ['%s connected' % sys.argv[0]])
        except (socket.timeout, socket.error):
            return False
        else:
            return True

    logging.info('connecting to excavator at %s:%d' % EXCAVATOR_ADDRESS)

    while not contact_excavator():
        sleep(5)
    while True:
        try:
            paying, ports = nicehash_multialgo_info()
        except urllib.error.URLError as err:
            logging.warning('failed to retrieve NiceHash stats: %s' % err.reason)
        except urllib.error.HTTPError as err:
            logging.warning('server error retrieving NiceHash stats: %s %s'
                            % (err.code, err.reason))
        except socket.timeout:
            logging.warning('failed to retrieve NiceHash stats: timed out')
        except (json.decoder.JSONDecodeError, KeyError):
            logging.warning('failed to parse NiceHash stats')
        else:
            for device in BENCHMARKS.keys():
                payrates = nicehash_mbtc_per_day(device, paying)
                best_algo = max(payrates.keys(), key=lambda algo: payrates[algo])

                if device not in worker_status:
                    logging.info('device %s initial algorithm is %s (%.2f mBTC/day)'
                                 % (device, best_algo, payrates[best_algo]))

                    dispatch_device(device, best_algo, ports)
                else:
                    current_algo = device_algorithm(device)

                    if current_algo != best_algo and \
                       (payrates[current_algo] == 0 or \
                        payrates[best_algo]/payrates[current_algo] >= 1.0 + PROFIT_SWITCH_THRESHOLD):
                        logging.info('switching device %s to %s (%.2f mBTC/day)'
                                     % (device, best_algo, payrates[best_algo]))

                        free_device(device)
                        dispatch_device(device, best_algo, ports)
        sleep(UPDATE_INTERVAL)

if __name__ == '__main__':
    main()