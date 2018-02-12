import requests, time, os, sys
import logging
import re
import json
import tempfile
import subprocess

import excavator_benchmark
import overclock


class Benchmark():
    def __init__(self, executable, device, algo, benchmark_length, clock, power):
        self.executable = executable
        self.device = device
        self.algo = algo
        self.benchmark_length = benchmark_length
        self.clock = clock
        self.power = power
        self.result = None

        self.dev_power = None
        self.dev_clock = None
        self.dev_temp = None
    
    def __str__(self):
        return "Benchmark(algo: %s, length: %d, clock: %s, power: %s)" % (self.algo, self.benchmark_length, str(self.clock) or "None", str(self.power) or "None")
    
    def csv(self):
        return ",".join([str(self.dev_power), str(self.dev_clock), str(self.dev_temp), str(self.result)])


def generate_tasks(executable, device, algo, benchmark_length, clock_range, power_range):

    logger = logging.getLogger(__name__)

    tasks = []
    for p in power_range:
        for c in clock_range:
            tasks.append(Benchmark(executable, device, algo, benchmark_length, c, p))
    
    return tasks

def run_benchmark(task: Benchmark):

    logger = logging.getLogger(__name__)

    dev = overclock.Device(task.device)
    dev.refresh()

    if(task.clock):
        dev.set_clock_offset(task.clock)

    if(task.power):
        dev.set_power(task.power)

    task.result = excavator_benchmark.benchmark(task.executable, task.device, task.algo, task.benchmark_length)
    
    dev.refresh()
    task.dev_clock = dev.get_clock_offset()
    task.dev_power = dev.get_power_limit()
    task.dev_temp = dev.get_temp()


def parse_range(spec):

    r_float = '[+-]?[0-9]*\.[0-9]+|[+-]?[0-9]+'
    m_scalar = re.match(r"("+r_float+")", spec)
    m_range = re.match(r"\[("+r_float+"):?("+r_float+")?:?("+r_float+")?\]", spec)

    if(m_scalar):
        return [float(m_scalar.group(1))]
    elif(m_range):
        first = float(m_range.group(1))
        step = 1
        last = first
        if(m_range.group(3)):
            last = float(m_range.group(3))
            step = float(m_range.group(2))
        elif(m_range.group(2)):
            last = float(m_range.group(2))
        return list(frange(first, last, step))
    else:
        raise Exception("Could not parse range: %s" % (spec))
            
def frange(x, y, jump):
    while x < y:
        yield x
        x += jump
    yield y


if(__name__ == "__main__"):

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])
    logger = logging.getLogger()

    import argparse
    parser = argparse.ArgumentParser(description='Run a single excavator benchmark')

    parser.add_argument("--algo", '-a', help='algorithm', required = True)
    parser.add_argument("--device", "-d", help='device number (default: 0)', default = 0, type=int)
    parser.add_argument("--length", '-l', help='length [s] (default: 100)', default = 100, type=int)
    parser.add_argument("--excavator-path", '-e', help='path to excavator executable (default: excavator)', default="excavator")

    parser.add_argument("--clock-range", '-c', help='clock offset value/range, example: [-50:10:150]')
    parser.add_argument("--power-range", '-p', help='power limit value/range, example: [200:10:300]')

    parser.add_argument("--csv", '-s', help='output comma separated data file')

    args = parser.parse_args()

    clock_range = parse_range(args.clock_range) if args.clock_range else [None]
    power_range = parse_range(args.power_range) if args.power_range else [None]

    tasks = generate_tasks(args.excavator_path, args.device, args.algo, args.length, clock_range, power_range)

    logger.info("Running %d bechmarks, with estimated total execution time: %.1f min", len(tasks), float(len(tasks) * (args.length+4))/ 60.0)

    output = ""
    progress = 1
    for t in tasks:
        logger.info("Task   (%d/%d): %s", progress, len(tasks), str(t))
        run_benchmark(t)
        logger.info("Result (%d/%d): %f H/s", progress, len(tasks), t.result)
        output = output + t.csv() + "\n"
        progress = progress + 1
        
    if(args.csv):
        with open(args.csv, "w") as f:
            f.write(output)
