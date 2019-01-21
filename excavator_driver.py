#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# pip3 install --user git+https://github.com/bodiroga/homie-python.git@homie-v3.0.0

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
import homie

import nvidia_smi
import excavator_api
import nicehash_api
import overclock
import ws_ipc
import benchmark_db

UPDATE_INTERVAL = 30
SPEED_INTERVAL = 2

# TODO read from config file
# TODO sync to nh update time
# TODO oc auto tune
# TODO Regulate power on high temps


# OC Auto tune:
# * Detect crash using nvidia_smi
# - Relaunch only on confirmed cause
# - Log temperature problems in oc db
# - Monitor perf caps when deciding strategy
# - Monitor excavator process using api
# - Verify excavator is fully dead before restaring



class Driver:

    def __init__(self, wallet, region, benchmarks, devices, name, oc_strat, oc_file, switching_threshold, run_excavator, ipc_port, autostart, db, mqtt_host, mqtt_name):

        self.wallet = wallet
        self.region = region
        self.benchmarks = benchmarks
        self.devices = devices
        self.name = name
        self.oc_file = oc_file
        self.switching_threshold = switching_threshold
        self.run_excavator = run_excavator
        self.ipc_port = ipc_port
        self.db = db

        self.state = Driver.State.INIT
        self.device_monitor = nvidia_smi.Monitor(data=["xidEvent", "temp"])
        self.excavator_proc = None
        self.ipc = None

        # Mqtt
        self.mqtt_host = mqtt_host
        self.mqtt_name = mqtt_name

        # Mqtt homie
        self.homie = None
        if self.mqtt_host:
            homie_config = {
                "HOST": mqtt_host,
                "PORT": 1883,
                "KEEPALIVE": 10,
                "USERNAME": "",
                "PASSWORD": "",
                "CA_CERTS": "",
                "DEVICE_NAME": mqtt_name,
                "DEVICE_ID": mqtt_name,
                "TOPIC": "homie"
            }
            self.homie = homie.Device(homie_config)
            logger.debug("a")
            self.homie_cooling = self.homie.addNode("cooing", "Cooling system", "temperature")
            self.homie_devices = {}


        # dict of device id -> overclocking device object
        self.devices_oc = {}
        # dict of device id -> settings
        self.device_settings = {}

        self.paying_current = None

        for d in self.devices:
            dev = Driver.DeviceSettings()
            dev.id = d
            dev.enabled = autostart
            dev.oc_strategy = oc_strat
            self.device_settings[d] = dev

        self.excavator = excavator_api.ExcavatorApi()

        self.oc_config = None

        for d in devices:
            self.devices_oc[d] = overclock.Device(d)
            self.devices_oc[d].refresh()

    class DeviceSettings:

        class OcStrategy:
            NONE = "none"
            FILE = "file"
            DB_BEST = "db_best"
            DB_SEARCH = "db_search"

        def __init__(self):
            self.id = 0
            self.enabled = False
            self.best_algo = None
            self.current_algo = None
            self.current_speed = 0.0
            self.paying = 0.0
            self.uuid = ""
            self.running = False

            self.oc_strategy = self.OcStrategy.NONE
            self.oc_session = None

    class OcSpec:
        def __init__(self):
            self.gpu_clock = None
            self.mem_clock = None
            self.power = None

        def equals(self, other):
            return self.gpu_clock == other.gpu_clock and self.mem_clock == other.mem_clock and self.power == other.power
        
        def keys(self):
            return ["gpu_clock", "mem_clock", "power"]

        def __str__(self):

            spec = [("%s: %i" % (k, self.__getattribute__(k))) for k in self.keys() if self.__getattribute__(k) != None]
            return "None" if len(spec) == 0 else ", ".join(spec)

    class OcStrategy:
        def __init__(self, device_uuid, algo):
            self.device_uuid = device_uuid
            self.algo = algo
            
        def get_spec(self):
            pass
        
        def refresh(self):
            pass
    
    class OcStrategyFile(OcStrategy):
        def __init__(self, device_uuid, algo, filename):
            Driver.OcStrategy.__init__(self, device_uuid, algo)
            self.filename = filename
            self.oc_config = None

            self.refresh()
        
        def refresh(self):
            self.oc_config = json.load(open(self.filename))
        
        def get_spec(self):
            spec = Driver.OcSpec()

            paths = [
                [self.device_uuid, self.algo], 
                [self.device_uuid, "default"], 
                ["default", self.algo], 
                ["default", "default"]
            ]

            for e in spec.keys():
                for p in paths:
                    if p[0] in self.oc_config and p[1] in self.oc_config[p[0]] and e in self.oc_config[p[0]][p[1]]:
                        spec.__setattr__(e, self.oc_config[p[0]][p[1]][e])
                        break

            return spec

    class OcSession:
        class State:
            INACTIVE = "inactive"
            WARMUP_1 = "warmup_1"
            WARMUP_2 = "warmup_2"
            ACTIVE = "active"
            FINISHING = "finishing"

        
        TIME_WARMUP_1 = 10
        TIME_WARMUP_2 = 20
        BENCHMARK_MIN_LENGTH = 100

        def __init__(self, strategy, dev, database, excavator):
            
            self.strategy = strategy
            self.dev = dev
            self.applied_oc = Driver.OcSpec()
            self.database = database
            self.excavator = excavator

            self.avg_full = 0
            self.benchmark_result = {
                "length": 0,
                "avg_full": 0,
                "current_speed": 0
            }

            self._set_state(self.State.INACTIVE)
            self.reset_timer()
        
        def end(self):
            self.set_finishing()

        def reset_timer(self):
            self.state_time_start = time.time()
            self.state_time_last = self.state_time_start

        def _set_state(self, state):
            self.state = state
            logging.info("[%s] Oc session state: %s", self.strategy.device_uuid, state)

        def set_warmup_1(self):
            self._set_state(self.State.WARMUP_1)
            self.reset_timer()

        def set_warmup_2(self):
            self._set_state(self.State.WARMUP_2)
            self.reset_timer()
            self.overclock()
        
        def set_active(self):
            self._set_state(self.State.ACTIVE)
            self.excavator.device_speed_reset(self.strategy.device_uuid)
            self.reset_timer()
            # Reset speed measurement?

        def set_finishing(self):

            # Write to db if state is ACTIVE
            dev_power = 0
            dev_clock = 0
            dev_power = 0
            
            try:
                dev_clock = self.dev.get_clock_offset()
            except overclock.NotSupportedException:
                dev_clock = 0
            
            try:
                dev_mem = self.dev.get_memory_offset()
            except overclock.NotSupportedException:
                dev_mem = 0

            try:
                dev_power = self.dev.get_power_offset()
            except overclock.NotSupportedException:
                dev_power = 0

            if self.database and \
                    self.state == self.State.ACTIVE and \
                    self.benchmark_result["length"] > self.BENCHMARK_MIN_LENGTH:
                logging.debug("Saving benchmark result: %s", str(self.benchmark_result))
                self.database.save(self.strategy.algo, 
                    self.strategy.device_uuid, 
                    "excavator", "1.5.14a", 
                    self.benchmark_result["avg_full"], 
                    dev_power, 
                    dev_clock, 
                    dev_mem, 
                    True, 
                    self.benchmark_result["length"])

            self._set_state(self.State.FINISHING)
            self.reset_timer()
            self.reset_overclock()

        def get_speeds(self):
            # TODO ERROR!
            return self.benchmark_result["length"] > 10

        def loop_active(self, current_speed):
            now = time.time()
            time_prev = self.state_time_last - self.state_time_start
            time_cuml = now - self.state_time_start
            time_step = now - self.state_time_last
            self.state_time_last = now

            self.avg_full = (self.avg_full * time_prev + current_speed * time_step) / time_cuml

            self.benchmark_result = {
                "length": time_cuml,
                "avg_full": self.avg_full,
                "current_speed": current_speed
            }

        def loop(self, current_speed):

            if self.state == self.State.INACTIVE:
                if time.time() - self.state_time_start > 0:
                    self.set_warmup_1()
            if self.state == self.State.WARMUP_1:
                if time.time() - self.state_time_start > self.TIME_WARMUP_1:
                    self.set_warmup_2()
            if self.state == self.State.WARMUP_2:
                if time.time() - self.state_time_start > self.TIME_WARMUP_2:
                    self.set_active()
            if self.state == self.State.ACTIVE:
                self.loop_active(current_speed)

        def overclock(self):
            self.strategy.refresh()
            spec = self.strategy.get_spec()

            logging.info("[%s] Applying Oc: %s", self.strategy.device_uuid, spec)

            if not self.applied_oc.equals(spec):

                if spec.gpu_clock:
                    self.dev.set_clock_offset(spec.gpu_clock)
                    logging.info("overclocking device %i, gpu_clock: %s" % (self.dev.device_number, str(spec.gpu_clock)))
                if spec.mem_clock:
                    self.dev.set_memory_offset(spec.mem_clock)
                    logging.info("overclocking device %i, mem_clock: %s" % (self.dev.device_number, str(spec.mem_clock)))
                if spec.power:
                    try:
                        self.dev.set_power_offset(spec.power)
                        logging.info("overclocking device %i, power: %s" % (self.dev.device_number, str(spec.power)))
                    except:
                        pass
                self.applied_oc = spec

        def reset_overclock(self):
            logging.info("[%s] Resetting Oc", self.strategy.device_uuid)

            # Reset overclocking
            if self.applied_oc.gpu_clock:
                self.dev.set_clock_offset(0)
                logging.info("overclocking device %i, gpu_clock: %s" % (self.dev.device_number, str(0)))
            if self.applied_oc.mem_clock:
                self.dev.set_memory_offset(0)
                logging.info("overclocking device %i, mem_clock: %s" % (self.dev.device_number, str(0)))
            if self.applied_oc.power:
                try:
                    self.dev.set_power_offset(0)
                    logging.info("overclocking device %i, power: %s" % (self.dev.device_number, str(0)))
                except:
                    pass
            self.dev.unset_performance_mode()

            self.applied_oc = Driver.OcSpec()


    class State:
        INIT = 0
        RUNNING = 1
        CRASHING = 2
        RESTARTING = 3

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


    def dispatch_device(self, device, algo):
        ds = self.device_settings[device]
        ds.current_algo = algo

        self.excavator.state_set(ds.uuid, algo, self.region, self.wallet, self.name)

        if ds.oc_strategy == Driver.DeviceSettings.OcStrategy.FILE:
            strategy = self.OcStrategyFile(ds.uuid, algo, self.oc_file)
            ds.oc_session = Driver.OcSession(strategy, self.devices_oc[device], self.db, self.excavator)

        ds.running = True

    def free_device(self, device):
        ds = self.device_settings[device]
        ds.current_algo = None

        if ds.oc_session:
            ds.oc_session.end()
            result = ds.oc_session.get_speeds()
            logging.debug('Benchmark results: %s', str(result))
            ds.oc_session = None

        self.excavator.state_set(ds.uuid, "", self.region, self.wallet, self.name)

        ds.running = False


    def cleanup(self):
        logging.info('Cleaning up')
        self.excavator.is_alive()

        self.ipc.stop()

        logging.info('Stopping mqtt')
        if self.homie is not None:
            #pass
            #del self.homie
            self.homie._exitus()
        logging.info('Stopped mqtt')

        try:
            self.device_monitor.stop()
        except Exception as e:
            logging.error("Error stopping devoce monitor: " + str(e))

        
        # Reset overclocking
        for device, ds in self.device_settings.items():
            if ds.running and ds.oc_session:
                ds.oc_session.end()

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

    def get_device_settings(self, uuid):
        return next(x for x in self.device_settings.values() if x.uuid == uuid)

    def homie_setup(self):

        self.homie.setFirmware("miner", "1.0.0")
        self.homie_cooling.addProperty("water-cold", name="Water cold", unit="ºC", datatype="float")
        self.homie_cooling.addProperty("water-hot", name="Water hot", unit="ºC", datatype="float")
        self.homie_cooling.addProperty("air-1", name="Air 1", unit="ºC", datatype="float")
        self.homie_cooling.addProperty("air-2", name="Air 2", unit="ºC", datatype="float")

        for ds in self.device_settings.values():
            name = "GPU "+str(ds.id)
            node = self.homie.addNode(ds.uuid.lower(), name, "gpu")
            node.addProperty("temperature", name=name+" Temperature", unit="ºC", datatype="float")
            node.addProperty("id", name=name+" Id", datatype="integer")
            node.addProperty("algo", name=name+" Algorithm", datatype="string")
            node.addProperty("speed", name=name+" Speed", unit="H/s", datatype="float")
            node.addProperty("paying", name=name+" Paying", unit="mBTC/d", datatype="float")
            node.addProperty("enabled", name=name+" Enabled", datatype="boolean").settable(self.homie_gen_lambda(ds.uuid))

            node.addProperty("oc-gpu", name=name+" Core clock offset", unit="Hz", datatype="integer")
            node.addProperty("oc-mem", name=name+" Memory clock offset", unit="Hz", datatype="integer")
            node.addProperty("oc-power", name=name+" Power offset", unit="W", datatype="integer")
            node.addProperty("oc-state", name=name+" Overclock state", datatype="enum", format="inactive,warmup_1,warmup_2,active,finishing")
            
            self.homie_devices[ds.uuid] = node

        self.homie.setup()

    def homie_gen_lambda(self, uuid):
        return lambda prop, value: self.homie_enable_device(uuid, value)

    def homie_enable_device(self, uuid, payload):
        ds = self.get_device_settings(uuid)
        logging.info("Got message for %s: %s", uuid, payload)
        if payload == "true":
            ds.enabled = True
        elif payload == "false":
            ds.enabled = False
        else:
            logging.error("Received unrecognized payload: %s", payload)

    def publish_device(self, device):
        ds = self.device_settings[device]
        hd = self.homie_devices[ds.uuid]

        hd.getProperty("id").update(device)
        hd.getProperty("algo").update("" if ds.current_algo is None else ds.current_algo)
        hd.getProperty("speed").update(ds.current_speed)
        hd.getProperty("paying").update(ds.paying)
        hd.getProperty("enabled").update("true" if ds.enabled else "false")

        if(ds.oc_session):
            hd.getProperty("oc-gpu").update(ds.oc_session.applied_oc.gpu_clock)
            hd.getProperty("oc-mem").update(ds.oc_session.applied_oc.mem_clock)
            hd.getProperty("oc-mem").update(ds.oc_session.applied_oc.mem_clock)
            hd.getProperty("oc-state").update(ds.oc_session.state)
        else:
            hd.getProperty("oc-gpu").update(0)
            hd.getProperty("oc-mem").update(0)
            hd.getProperty("oc-power").update(0)
            hd.getProperty("oc-state").update("inactive")

    def publish_devices(self):
        for device, ds in self.device_settings.items():          
            self.publish_device(device)

    def run(self):

        # Start IPC to receive remote commands
        self.ipc = ws_ipc.IpcServer(self.ipc_port)
        self.ipc.start()

        # Get gpu info
        for device, ds in self.device_settings.items():
            ds.uuid = nvidia_smi.device(device)["uuid"]

        # Homie setup
        if self.mqtt_host:
            self.homie_setup()

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
                #logging.debug("Device event: "+str(event))

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
                    
                    if ds.oc_session:
                        ds.oc_session.loop(ds.current_speed)

                    ds.paying = self.nicehash_mbtc_algo_per_day(ds.current_algo, ds.current_speed)
                    self.ipc.publish({
                            "type": "device.algo",
                            "device_id": device,
                            "device_uuid": ds.uuid,
                            "algo": ds.current_algo,
                            "speed": ds.current_speed,
                            "paying": ds.paying
                        })
                    
                self.publish_devices()



            # Algorithm switching
            if self.state == Driver.State.RUNNING and now > last_nh_update + UPDATE_INTERVAL:
                last_nh_update = now

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

                            self.publish_device(device)

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
                        self.publish_device(device)

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
    parser.add_argument("--auto-tune-devices", "-u", help='enable overclocking auto tune for given devices', type=str)
    parser.add_argument("--excavator", "-e", help='launch excavator automatically', action='store_true')
    parser.add_argument("--ipc-port", "-p", help='port to expose ipc interface on', type=int, default=8082)
    parser.add_argument("--debug", "-g", help='enablbe debug logging', action='store_true')
    parser.add_argument("--autostart", "-m", help='autostart mining', action='store_true')
    parser.add_argument("--db", help='benchmark db directory name')
    parser.add_argument("--mqtt-host", help='mqtt hostname')
    parser.add_argument("--mqtt-name", help='mqtt name', default="miner-01")

    parser.add_argument("--overclock", "-o", help="initial overclocing strategy [file, db_best, db_search]")
    parser.add_argument("--overclock-file", "-f", help="overclocing spec file for file strategy")

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
    db = benchmark_db.BenchmarkDb(args.db) if args.db else None

    def sigint_handler(signum, frame):
        driver.cleanup()
        sys.exit(0)

    oc_strategy = Driver.DeviceSettings.OcStrategy.NONE
    if args.overclock == "file":
        oc_strategy = Driver.DeviceSettings.OcStrategy.FILE
        if not args.overclock_file:
            parser.error("--overclock 'file' requires --overclock-file to be specified")

    if args.overclock == "db_best":
        oc_strategy = Driver.DeviceSettings.OcStrategy.DB_BEST
    if args.overclock == "db_search":
        oc_strategy = Driver.DeviceSettings.OcStrategy.DB_SEARCH

    driver = Driver(args.address, args.region, benchmarks, devices, args.worker, oc_strategy, args.overclock_file, args.threshold, args.excavator, args.ipc_port, args.autostart, db, args.mqtt_host, args.mqtt_name)
    signal.signal(signal.SIGINT, sigint_handler)

    driver.run()


    
