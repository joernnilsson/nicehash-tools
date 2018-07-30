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
import prctl
from pprint import pprint
from time import sleep
import urllib.error
import urllib.request

import nvidia_smi
import excavator_api
import nicehash_api
import overclock
import ws_ipc

UPDATE_INTERVAL = 30
SPEED_INTERVAL = 2

# TODO read from config file
# TODO sync to nh update time
# TODO oc auto tune


# OC Auto tune:
# * Detect crash using nvidia_smi
# - Relaunch only on confirmed cause
# - Log temperature problems in oc db
# - Monitor perf caps when deciding strategy
# - Monitor excavator process using api
# - Verify excavator is fully dead before restaring


class Driver:

    def __init__(self, wallet, region, benchmarks, devices, name, oc_spec, switching_threshold, run_excavator, ipc_port, autostart):

        self.wallet = wallet
        self.region = region
        self.benchmarks = benchmarks
        self.devices = devices
        self.name = name
        self.oc_spec = oc_spec
        self.switching_threshold = switching_threshold
        self.run_excavator = run_excavator
        self.ipc_port = ipc_port

        self.state = Driver.State.INIT
        self.device_monitor = nvidia_smi.Monitor(data=["xidEvent", "temp"])
        self.excavator_proc = None
        self.ipc = None

        # dict of device id -> overclocking device object
        self.devices_oc = {}
        # dict of device id -> current overclocking settings
        self.devices_oc_spec = {}
        # dict of device id -> settings
        self.device_settings = {}

        self.paying_current = None

        for d in self.devices:
            dev = Driver.DeviceSettings()
            dev.enabled = autostart
            self.device_settings[d] = dev

        self.excavator = excavator_api.ExcavatorApi()

        self.oc_config = None

        if self.oc_spec is not None:
            for d in devices:
                self.devices_oc[d] = overclock.Device(d)
                self.devices_oc[d].refresh()

    class DeviceSettings:
        def __init__(self):
            self.enabled = False
            self.best_algo = None
            self.current_algo = None
            self.current_speed = 0.0
            self.paying = 0.0
            self.uuid = ""
            self.running = False

    class State:
        INIT = 0
        RUNNING = 1
        CRASHING = 2
        RESTARTING = 3


    def reaload_oc(self):
        if self.oc_spec is not None:
            self.oc_config = json.load(open(self.oc_spec))

    def get_device_oc(self, device, algo):  
        uuid = nvidia_smi.device(device)["uuid"]
        spec = {
                "gpu_clock": None,
                "mem_clock": None,
                "power": None
            }

        paths = [
            [uuid, algo], 
            [uuid, "default"], 
            ["default", algo], 
            ["default", "default"]
        ]

        for e in spec.keys():
            for p in paths:
                if p[0] in self.oc_config and p[1] in self.oc_config[p[0]] and e in self.oc_config[p[0]][p[1]]:
                    spec[e] = self.oc_config[p[0]][p[1]][e]
                    break

        return spec

    def nicehash_mbtc_algo_per_day(self, algo, speed):
        if not algo:
            return 0.0
        return self.paying_current[algo.lower()]*speed*(24*60*60)*1e-11

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

    def overclock(self, device, algo):
        if self.oc_spec is not None:
            spec = self.get_device_oc(device, algo)
            
            if spec["gpu_clock"]:
                self.devices_oc[device].set_clock_offset(spec["gpu_clock"])
                logging.info("overclocking device %i, gpu_clock: %s" % (device, str(spec["gpu_clock"])))
            if spec["mem_clock"]:
                self.devices_oc[device].set_memory_offset(spec["mem_clock"])
                logging.info("overclocking device %i, mem_clock: %s" % (device, str(spec["mem_clock"])))
            if spec["power"]:
                try:
                    self.devices_oc[device].set_power_offset(spec["power"])
                    logging.info("overclocking device %i, power: %s" % (device, str(spec["power"])))
                except:
                    pass
            self.devices_oc_spec[device] = spec

    def reset_overclock(self, device):
        # Reset overclocking
        if self.oc_spec is not None and self.devices_oc_spec[device] is not None:
            spec = self.devices_oc_spec[device]
            if spec["gpu_clock"]:
                self.devices_oc[device].set_clock_offset(0)
            if spec["mem_clock"]:
                self.devices_oc[device].set_memory_offset(0)
            if spec["power"]:
                try:
                    self.devices_oc[device].set_power_offset(0)
                except:
                    pass
            self.devices_oc[device].unset_performance_mode()
        
            self.devices_oc_spec[device] = None

    def dispatch_device(self, device, algo):
        ds = self.device_settings[device]
        ds.current_algo = algo

        self.excavator.state_set(ds.uuid, algo, self.region, self.wallet, self.name)

        self.overclock(device, algo)

        ds.running = True

    def free_device(self, device):
        ds = self.device_settings[device]
        ds.current_algo = None

        # Reset overclocking
        self.reset_overclock(device)

        self.excavator.state_set(ds.uuid, "", self.region, self.wallet, self.name)

        ds.running = False


    def cleanup(self):
        logging.info('Cleaning up!')
        self.excavator.is_alive()

        self.ipc.stop()

        try:
            self.device_monitor.stop()
        except Exception as e:
            logging.error("Error stopping devoce monitor: " + str(e))

        
        # Reset overclocking
        for device, ds in self.device_settings.items():
            if ds.running:
                self.reset_overclock(device)

        try:
            self.excavator.stop()
        except Exception as e:
            logging.warn("Warning stopping excavator: " + str(e))

        #if self.excavator_proc:
            #logging.info('Termniating excavator')
            #self.excavator_proc.terminate()
            #self.excavator_proc.wait(timeout=1.0)
            #logging.info('Killing excavator')
            #self.excavator_proc.kill()
            #self.excavator_proc.wait(timeout=1.0)

    def run(self):

        # Start IPC to receive remote commands
        self.ipc = ws_ipc.IpcServer(self.ipc_port)
        self.ipc.start()

        # Get gpu info
        for device, ds in self.device_settings.items():
            ds.uuid = nvidia_smi.device(device)["uuid"]
        

        # Start excavator
        if self.run_excavator:
            self.excavator_proc = subprocess.Popen(['./temperature_guard.py', '80', 'excavator'], preexec_fn=lambda: prctl.set_pdeathsig(signal.SIGKILL))
            
        logging.info('connecting to excavator')
        while not self.excavator.is_alive():
            sleep(5)

        #self.excavator.subscribe(self.region, self.wallet, self.name)
        self.device_monitor.start()
        last_nh_update = 0.0
        last_speed_update = 0.0
        self.state = Driver.State.RUNNING

        while True:

            now = time.time()

            # Read device events
            try:
                event = self.device_monitor.get_event(block=False)
                #event = self.device_monitor.get_event(block=True, timeout=0.1)
                logging.debug("Device event: "+str(event))

                if(event["type"] == "xidEvent"):
                    if(event["value"] == 43):
                        logging.error("Gpu %i: crashed! Waiting for signal 45 (xid: %i)" % (event["id"], event["value"]))
                        self.state = Driver.State.CRASHING
                        self.cleanup()
                    elif(event["value"] == 45):
                        logging.info("Gpu %i: recovered after crash (xid: %i)" % (event["id"], event["value"]))
                        self.state = Driver.State.RESTARTING
                    else:
                        logging.error("Gpu %i: unhandled error (xid: %i)" % (event["id"], event["value"]))

            except self.device_monitor.Empty:
                pass

            # Read IPC events
            try:
                event = self.ipc.get_event(block=False)
                response = None
                logging.debug("IPC event: "+str(event))

                d = event.data
                if d["cmd"] == "device.enable":
                    self.device_settings[d["device_id"]].enabled = d["enable"]
                elif d["cmd"] == "publish.state":
                    for device, ds in self.device_settings.items():
                        self.ipc.publish({
                                "type": "device.algo",
                                "device_id": device,
                                "device_uuid": ds.uuid,
                                "algo": ds.current_algo,
                                "speed": ds.current_speed,
                                "paying": ds.paying
                            })

                event.respond(response)

            except ws_ipc.IpcServer.Empty:
                pass
            except Exception as e:
                logging.error("Error from IPC Server: "+str(e))
                import traceback
                print(traceback.format_exc(e))

            sleep(0.1)

            # Update device speeds
            if self.state == Driver.State.RUNNING and now > last_speed_update + SPEED_INTERVAL:
                last_speed_update = now
                for device, ds in self.device_settings.items():
                    speeds = self.excavator.device_speeds(device)
                    ds.current_speed = speeds[ds.current_algo] if ds.current_algo in speeds else 0.0
                    ds.paying = self.nicehash_mbtc_algo_per_day(ds.current_algo, ds.current_speed)
                    self.ipc.publish({
                            "type": "device.algo",
                            "device_id": device,
                            "device_uuid": ds.uuid,
                            "algo": ds.current_algo,
                            "speed": ds.current_speed,
                            "paying": ds.paying
                        })



            # Algorithm switching
            if self.state == Driver.State.RUNNING and now > last_nh_update + UPDATE_INTERVAL:
                last_nh_update = now

                self.reaload_oc()

                if not self.excavator.is_alive():
                    logging.error("Excavator is not alive, exiting")
                    self.cleanup()
                    return

                try:
                    self.paying_current = nicehash_api.multialgo_info()
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
                    for device in self.devices:
                        payrates = self.nicehash_mbtc_per_day(device, self.paying_current)

                        best_algo = max(payrates.keys(), key=lambda algo: payrates[algo])
                        current_algo = self.device_settings[device].current_algo

                        best_pay = payrates[best_algo]
                        current_pay = payrates[current_algo] if current_algo else 0.0

                        if best_pay > current_pay * (1.0 + self.switching_threshold):
                            self.device_settings[device].best_algo = best_algo
                            

            # Apply device settings
            for device in self.devices:
                ds = self.device_settings[device]
                if ds.enabled:
                    if ds.best_algo:
                        if ds.current_algo != ds.best_algo:
                            payrates = self.nicehash_mbtc_per_day(device, self.paying_current)
                            logging.info('Switching device %s to %s (%.2f mBTC/day)' % (device, ds.best_algo, payrates[ds.best_algo]))
                            ds.current_speed = 0.0
                            ds.current_pay = 0.0
                            self.ipc.publish({
                                "type": "device.algo",
                                "device_id": device,
                                "device_uuid": ds.uuid,
                                "algo": best_algo,
                                "speed": ds.current_speed,
                                "paying": ds.paying
                            })

                            if ds.running:
                                self.free_device(device)
        
                            self.dispatch_device(device, best_algo)

                else:
                    if ds.running:
                        logging.info("Disabling device %i" % (device))
                        ds.current_speed = 0.0
                        ds.current_pay = 0.0
                        self.ipc.publish({
                                "type": "device.algo",
                                "device_id": device,
                                "device_uuid": ds.uuid,
                                "algo": None,
                                "speed": ds.current_speed,
                                "paying": ds.paying
                            })
                        self.free_device(device)


            #sleep(UPDATE_INTERVAL)
            sleep(0.1)



def parse_devices(spec):
    if spec == "all":
        devices = nvidia_smi.devices()
        return [x["id"] for x in devices]
            
    else:
        return [int(x) for x in spec.split(",")]


def read_benchmarks(filename, devices):
    # TODO Check if file exists
    data = json.load(open(filename))

    bms = {}
    for d in devices:
        bms[d] = data

    return bms

if __name__ == '__main__':

    import argparse
    parser = argparse.ArgumentParser(description='Excavator client')

    parser.add_argument("--devices", "-d", help='devices to use (default: all)', default = "all", type=str)
    parser.add_argument("--region", '-r', help='region (default eu)', default = "eu")
    parser.add_argument("--worker", '-w', help='worker name', default = "wr02")
    parser.add_argument("--address", '-a', help='wallet address', default="3FkaDHat56SfuJaueRo9CCUM1rCGMK2coQ")
    parser.add_argument("--threshold", '-t', help='switching threshold ratio (default: 0.02)', default=0.02, type=float)
    parser.add_argument("--benchmark", '-b', help='benchmark file (default: benchmark.json)', default = "benchmark.json")
    parser.add_argument("--overclock", "-o", help="file containing overclocking specs")
    parser.add_argument("--auto-tune-devices", "-u", help='enable overclocking auto tune for given devices', type=str)
    parser.add_argument("--excavator", "-e", help='launch excavator automatically', action='store_true')
    parser.add_argument("--ipc-port", "-p", help='port to expose ipc interface on', type=int, default=8082)
    parser.add_argument("--debug", "-g", help='enablbe debug logging', action='store_true')
    parser.add_argument("--autostart", "-m", help='autostart mining', action='store_true')

    args = parser.parse_args()

    loglevel = logging.DEBUG if args.debug else logging.INFO

    logging.basicConfig(
        level=loglevel,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])
    logger = logging.getLogger(__name__)

    devices = parse_devices(args.devices)
    benchmarks = read_benchmarks(args.benchmark, devices)

    def sigint_handler(signum, frame):
        driver.cleanup()
        sys.exit(0)

    driver = Driver(args.address, args.region, benchmarks, devices, args.worker, args.overclock, args.threshold, args.excavator, args.ipc_port, args.autostart)
    signal.signal(signal.SIGINT, sigint_handler)

    driver.reaload_oc()

    driver.run()


    
