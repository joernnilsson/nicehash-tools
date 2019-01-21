import requests, time, os, sys
import logging
import prctl
import http.cookiejar
import imaplib
import re
import json
import tempfile
import subprocess
import overclock_session
import overclock
import excavator_api
import nvidia_smi

class Benchmark():
    def __init__(self, device, algo, benchmark_length, oc_spec=None):
        self.device = device
        self.algo = algo
        self.benchmark_length = benchmark_length
        self.result = []

        self.oc_spec = oc_spec
        if not self.oc_spec:
            self.oc_spec = overclock_session.OcSpec()

        self.dev_uuid = "uuid"
        self.dev_power = None
        self.dev_clock = None
        self.dev_mem = None
        self.dev_temp = None
    
    def __str__(self):
        return "Benchmark(algo: %s, length: %d, clock: %s, power: %s, mem: %s)" % (self.algo, self.benchmark_length, str(self.oc_spec.gpu_clock) or "None", str(self.oc_spec.power) or "None", str(self.oc_spec.mem_clock) or "None")
    
    def csv(self):
        return ",".join([str(self.dev_power), str(self.dev_clock), str(self.dev_temp), str(self.result[0]), str(self.dev_mem)])


def run_benchmark(task: Benchmark, oc_warmup_1=2, oc_warmup_2=20):

    device_oc = overclock.Device(task.device)
    device_oc.refresh()

    # Start excavator
    excavator = excavator_api.ExcavatorApi()
    excavator_proc = subprocess.Popen(['./temperature_guard.py', '80', 'excavator'])

    logging.info('connecting to excavator')
    while not excavator.is_alive():
        time.sleep(0.5)

    uuid = nvidia_smi.device(task.device)["uuid"]
    excavator.state_set(uuid, "benchmark-"+task.algo, "eu", "3FkaDHat56SfuJaueRo9CCUM1rCGMK2coQ", "wr03")

    strategy = overclock_session.OcStrategyStatic(uuid, task.algo, task.oc_spec)
    oc_session = overclock_session.OcSession(strategy, device_oc, None, excavator)

    while(oc_session.get_result()["length"] < task.benchmark_length):
        speeds = excavator.device_speeds(task.device)
        current_speed = speeds[task.algo] if task.algo in speeds else 0.0
        oc_session.loop(current_speed)
        time.sleep(1.0)

    oc_session.end()
    result = oc_session.get_result()
    logging.debug('Benchmark results: %s', str(result))

    excavator.stop()
    excavator.quit()

    excavator_proc.terminate()
    excavator_proc.wait()

    # TODO Handle dual algos
    return [result["avg_full"]]



if(__name__ == "__main__"):

    import argparse
    parser = argparse.ArgumentParser(description='Run a single excavator benchmark')

    parser.add_argument("--algo", '-a', help='algorithm', required = True)
    parser.add_argument("--device", "-d", help='device number (default: 0)', default = 0, type=int)
    parser.add_argument("--length", '-l', help='length [s] (default: 100)', default = 100, type=int)

    parser.add_argument("--oc-gpu", help='gpu clock offset', default = None, type=int)
    parser.add_argument("--oc-mem", help='memory clock offset', default = None, type=int)
    parser.add_argument("--oc-power", help='memory clock offset', default = None, type=int)
    parser.add_argument("--oc-warmup-1", help='warmup period 1', default = 2, type=int)
    parser.add_argument("--oc-warmup-2", help='warmup period 2', default = 20, type=int)
    
    parser.add_argument("--excavator-path", '-e', help='path to excavator executable (default: excavator)', default="excavator")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])
    logger = logging.getLogger(__name__)


    oc_spec = overclock_session.OcSpec(args.oc_gpu, args.oc_mem, args.oc_power)
    task = Benchmark(args.device, args.algo, args.length, oc_spec)


    logger.info("Running %s benchmark for %d seconds", args.algo, args.length)
    rates = run_benchmark(task)
    logger.info("Benchmark finished, %s rate: [%s] H/s", args.algo, ", ".join(str(x) for x in rates))
