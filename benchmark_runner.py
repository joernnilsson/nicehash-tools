import requests, time, os, sys
import logging
import re
import json
import tempfile
import subprocess

import excavator_benchmark
import overclock
import benchmark_db


class Benchmark():
    def __init__(self, executable, device, algo, benchmark_length, clock, power, mem):
        self.executable = executable
        self.device = device
        self.algo = algo
        self.benchmark_length = benchmark_length
        self.clock = clock
        self.power = power
        self.mem = mem
        self.result = []

        self.dev_uuid = "uuid"
        self.dev_power = None
        self.dev_clock = None
        self.dev_mem = None
        self.dev_temp = None
    
    def __str__(self):
        return "Benchmark(algo: %s, length: %d, clock: %s, power: %s, mem: %s)" % (self.algo, self.benchmark_length, str(self.clock) or "None", str(self.power) or "None", str(self.mem) or "None")
    
    def csv(self):
        return ",".join([str(self.dev_power), str(self.dev_clock), str(self.dev_temp), str(self.result[0]), str(self.dev_mem)])

algorithms = [
    "equihash",
    "pascal",
    "decred",
    "blake2s",
    "daggerhashimoto",
    "lyra2rev2",
    "daggerhashimoto_decred",
    "daggerhashimoto_pascal",
    "keccak",
    "neoscrypt",
    "cryptonightV7",
    "lyra2z",
    "x16r"
    ]


def generate_tasks(executable, device, algos, benchmark_length, clock_range, power_range, mem_range):

    logger = logging.getLogger(__name__)

    tasks = []
    for a in algos:
        for p in power_range:
            for c in clock_range:
                for m in mem_range:
                    tasks.append(Benchmark(executable, device, a, benchmark_length, c, p, m))
    
    return tasks

def run_benchmark(task: Benchmark):

    logger = logging.getLogger(__name__)

    dev = overclock.Device(task.device)
    dev.refresh()

    task.dev_uuid = dev.get_uuid()

    if(task.clock != None):
        dev.set_clock_offset(task.clock)

    if(task.power != None):
        dev.set_power_offset(task.power)

    if(task.mem != None):
        dev.set_memory_offset(task.mem)

    task.result = excavator_benchmark.benchmark(task.executable, task.device, task.algo, task.benchmark_length)
    
    dev.refresh()
    try:
        task.dev_clock = dev.get_clock_offset()
    except overclock.NotSupportedException:
        task.dev_clock = 0
    
    try:
        task.dev_mem = dev.get_memory_offset()
    except overclock.NotSupportedException:
        task.dev_mem = 0

    try:
        task.dev_power = dev.get_power_offset()
    except overclock.NotSupportedException:
        task.dev_power = 0

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

def greater(a, b):
    return a[0] > b[0]

if(__name__ == "__main__"):

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])
    logger = logging.getLogger()

    import argparse
    parser = argparse.ArgumentParser(description='Run a single excavator benchmark')

    parser.add_argument("--algorithms", '-a', help='algorithms, comma separated (or "all")', required = True)
    parser.add_argument("--device", "-d", help='device number (default: 0)', default = 0, type=int)
    parser.add_argument("--length", '-l', help='length [s] (default: 100)', default = 100, type=int)
    parser.add_argument("--excavator-path", '-e', help='path to excavator executable (default: excavator)', default="excavator")

    parser.add_argument("--clock-range", '-c', help='gpu clock offset value/range, example: [-50:10:150]')
    parser.add_argument("--mem-range", '-m', help='memory clock offset value/range, example: [200:10:300]')
    parser.add_argument("--power-range", '-p', help='power limit value/range, example: [200:10:300]')

    parser.add_argument("--csv", '-s', help='output comma separated data file')
    parser.add_argument("--json", '-j', help='output json file suitable for excavator driver')
    parser.add_argument("--db", '-b', help='output to directory db')

    args = parser.parse_args()

    clock_range = parse_range(args.clock_range) if args.clock_range != None else [None]
    power_range = parse_range(args.power_range) if args.power_range != None else [None]
    mem_range = parse_range(args.mem_range) if args.mem_range != None else [None]

    algos = algorithms if args.algorithms == "all" else args.algorithms.split(",")

    tasks = generate_tasks(args.excavator_path, args.device, algos, args.length, clock_range, power_range, mem_range)

    logger.info("Running %d bechmarks, with estimated total execution time: %.1f min", len(tasks), float(len(tasks) * (args.length+4))/ 60.0)

    db = None
    if(args.db):
        db = benchmark_db.BenchmarkDb(args.db)

    output = ""
    top = {}
    progress = 1
    for t in tasks:
        logger.info("Task   (%d/%d): %s", progress, len(tasks), str(t))
        run_benchmark(t)
        logger.info("Result (%d/%d): %s H/s", progress, len(tasks), ", ".join(str(x) for x in t.result))
        output = output + t.csv() + "\n"

        if(not t.algo in top or top[t.algo] > t.result[0]):
            top[t.algo] = t.result[0] if len(t.result) == 1 else t.result
            
        progress = progress + 1

        if db:
            if len(t.result) > 1:
                raise Exception("Multi algo not supported by BenchmarkDb")

            # TODO read miner and version
            db.save(t.algo, t.dev_uuid, "excavator", "1.5.11", t.result[0], t.dev_power, t.dev_clock, t.dev_mem, True, t.benchmark_length)

    if(args.csv):
        with open(args.csv, "w") as f:
            f.write(output)

    if(args.json):
        with open(args.json, "w") as f:
            out = json.dumps(top, indent=4, sort_keys=True, separators=(',', ': '))
            logger.debug("Generated json: \n%s", out)
            f.write(out)

