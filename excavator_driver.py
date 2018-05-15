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
from pprint import pprint
from time import sleep
import urllib.error
import urllib.request

import nvidia_smi
import excavator_api
import nicehash_api
import overclock

UPDATE_INTERVAL = 30

# TODO read from config file
# TODO sync to nh update time
# TODO oc auto tune

class Job:
    def __init__(self, device, algo, oc = None):
        pass

class Driver:

    def __init__(self, wallet, region, benchmarks, devices, name, oc_spec, switching_threshold):

        self.wallet = wallet
        self.region = region
        self.benchmarks = benchmarks
        self.devices = devices
        self.name = name
        self.oc_spec = oc_spec
        self.switching_threshold = switching_threshold

        # dict of algorithm name -> (excavator id, [attached devices])
        self.algorithm_status = {}
        # dict of device id -> excavator worker id
        self.worker_status = {}
        # dict ov device id -> overclocking settings
        self.devices_oc = {}

        self.device_algorithm = lambda device: [a for a in self.algorithm_status.keys() if
                                        device in self.algorithm_status[a]][0]

        self.excavator = excavator_api.ExcavatorApi()

        self.oc_config = None

        if self.oc_spec is not None:
            for d in devices:
                self.devices_oc[d] = overclock.Device(d)
                self.devices_oc[d].refresh()


    def reaload_oc(self):
        self.oc_config = json.load(open(self.oc_spec))

    def get_device_oc(self, device, algo):  
        uuid = nvidia_smi.device(device)["uuid"]
        if uuid in self.oc_config and algo in self.oc_config[uuid]:
            return self.oc_config[uuid][algo]
        elif uuid in self.oc_config and "default" in self.oc_config[uuid]:
            return self.oc_config[uuid]["default"]
        elif "default" in self.oc_config and algo in self.oc_config["default"]:
            return self.oc_config["default"][algo]
        elif "default" in self.oc_config and "default" in self.oc_config["default"]:
            return self.oc_config["default"]["default"]
        else:
            return {
                "gpu_clock": 0,
                "mem_clock": 0,
                "power": 0
            }

    def nicehash_mbtc_per_day(self, device, paying):
        """Calculates the BTC/day amount for every algorithm.

        device -- excavator device id for benchmarks
        paying -- algorithm pay information from NiceHash
        """

        bms = self.benchmarks[device]
        pay = lambda algo, speed: paying[algo.lower()]*speed*(24*60*60)*1e-11
        def pay_benched(algo):
            if '_' in algo:
                return sum([pay(multi_algo, bms[algo][i]) for
                            i, multi_algo in enumerate(algo.split('_'))])
            else:
                return pay(algo, bms[algo])

        return dict([(algo, pay_benched(algo)) for algo in bms.keys()])


    def dispatch_device(self, device, algo, ports):
        if algo in self.algorithm_status:
            self.algorithm_status[algo].append(device)
        else:
            response = self.excavator.algo_add(algo)
            self.algorithm_status[algo] = [device]

        response = self.excavator.worker_add(algo, device)
        self.worker_status[device] = response

        # Apply overclocking
        if self.oc_spec is not None:
            spec = self.get_device_oc(device, algo)
            logging.info("overclocking device %i, gpu_clock: %i, power: %i" % (device, spec["gpu_clock"], spec["power"]))
            self.devices_oc[device].set_clock_offset(spec["gpu_clock"])
            try:
                self.devices_oc[device].set_power_offset(spec["power"])
            except:
                pass

    def free_device(self, device):

        # Reset overclocking
        if self.oc_spec is not None:
            self.devices_oc[device].set_clock_offset(0)
            try:
                self.devices_oc[device].set_power_offset(0)
            except:
                pass
            self.devices_oc[device].unset_performance_mode()

        algo = self.device_algorithm(device)
        self.algorithm_status[algo].remove(device)
        worker_id = self.worker_status[device]
        self.worker_status.pop(device)

        self.excavator.worker_free(worker_id)

        if len(self.algorithm_status[algo]) == 0:
            self.algorithm_status.pop(algo)
            self.excavator.algo_remove(algo)

    def cleanup(self):
        logging.info('cleaning up!')
        active_devices = list(self.worker_status.keys())
        for device in active_devices:
            self.free_device(device)
        self.excavator.stop()

    def start(self):

        logging.info('connecting to excavator at')
        while not self.excavator.is_alive():
            sleep(5)

        self.excavator.subscribe(self.region, self.wallet, self.name)

        while True:
            try:
                paying, ports = nicehash_api.multialgo_info()
            except urllib.error.HTTPError as err:
                logging.warning('server error retrieving NiceHash stats: %s %s' % (err.code, err.reason))
            except urllib.error.URLError as err:
                logging.warning('failed to retrieve NiceHash stats: %s' % err.reason)
            except socket.timeout:
                logging.warning('failed to retrieve NiceHash stats: timed out')
            except (json.decoder.JSONDecodeError, KeyError):
                logging.warning('failed to parse NiceHash stats')
            else:
                self.reaload_oc()
                for device in self.benchmarks.keys():
                    payrates = self.nicehash_mbtc_per_day(device, paying)
                    best_algo = max(payrates.keys(), key=lambda algo: payrates[algo])

                    if device not in self.worker_status:
                        logging.info('device %s initial algorithm is %s (%.2f mBTC/day)'
                                    % (device, best_algo, payrates[best_algo]))

                        self.dispatch_device(device, best_algo, ports)

                    else:
                        current_algo = self.device_algorithm(device)

                        if current_algo != best_algo and \
                        (payrates[current_algo] == 0 or \
                            payrates[best_algo]/payrates[current_algo] >= 1.0 + self.switching_threshold):
                            logging.info('switching device %s to %s (%.2f mBTC/day)'
                                        % (device, best_algo, payrates[best_algo]))

                            self.free_device(device)

                            self.dispatch_device(device, best_algo, ports)

            sleep(UPDATE_INTERVAL)




def parse_devices(spec):
    if spec == "all":
        devices = nvidia_smi.devices()
        return [x["id"] for x in devices]
            
    else:
        return [int(x) for x in spec.split(",")]


def read_benchmarks(filename):
    # TODO Check if file exists
    data = json.load(open(filename))

    bms = {}
    for d in nvidia_smi.devices():
        bms[d["id"]] = data

    return bms

if __name__ == '__main__':

    import argparse
    parser = argparse.ArgumentParser(description='Excavator client')

    parser.add_argument("--devices", "-d", help='devices to use (default: all)', default = "all", type=str)
    parser.add_argument("--region", '-r', help='region (default eu)', default = "eu")
    parser.add_argument("--worker", '-w', help='worker name', default = "worker-02")
    parser.add_argument("--address", '-a', help='wallet address', default="3FkaDHat56SfuJaueRo9CCUM1rCGMK2coQ")
    parser.add_argument("--threshold", '-t', help='switching threshold ratio (default: 0.02)', default=0.02, type=float)
    parser.add_argument("--benchmark", '-b', help='benchmark file (default: benchmark.json)', default = "benchmark.json")
    parser.add_argument("--overclock", "-o", help="file containing overclocking specs")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])
    logger = logging.getLogger(__name__)

    devices = parse_devices(args.devices)
    benchmarks = read_benchmarks(args.benchmark)

    def sigint_handler(signum, frame):
        driver.cleanup()
        sys.exit(0)

    driver = Driver(args.address, args.region, benchmarks, devices, args.worker, args.overclock, args.threshold)
    signal.signal(signal.SIGINT, sigint_handler)

    driver.start()


    
