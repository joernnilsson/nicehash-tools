import requests, time, os, sys
import logging
import re
import json
import tempfile
import subprocess

import excavator_benchmark
import excavator_api
import overclock
import overclock_session
import benchmark_db
import nvidia_smi

algorithms = [
    "equihash",
    "decred",
    "blake2s",
    "daggerhashimoto",
    "lyra2rev2",
    "daggerhashimoto_decred",
    "keccak",
    "neoscrypt",
    "cryptonightV7",
    "lyra2z",
    "x16r",
    "cryptonightV8"
    #"skunk"
    ]


def generate_tasks_oc_range(device, algos, benchmark_length, clock_range, power_range, mem_range):

    tasks = []
    for a in algos:
        for p in power_range:
            for c in clock_range:
                for m in mem_range:
                    tasks.append(excavator_benchmark.Benchmark(device, a, benchmark_length, overclock_session.OcSpec(c, m, p)))
    
    return tasks

def generate_tasks_oc_file(device, algos, benchmark_length, file):

    tasks = []
    uuid = nvidia_smi.device(device)["uuid"]
    for a in algos:
        os = overclock_session.OcStrategyFile(uuid, a, file)
        tasks.append(excavator_benchmark.Benchmark(device, a, benchmark_length, os.get_spec()))
    
    return tasks

def run_benchmark(task: excavator_benchmark.Benchmark):

    dev = overclock.Device(task.device)
    dev.refresh()

    task.dev_uuid = dev.get_uuid()

    task.result = excavator_benchmark.run_benchmark(task)
        
    dev.refresh()

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

    parser.add_argument("--overclock-file", "-f", help="overclocing spec file for file strategy")

    parser.add_argument("--clock-range", '-c', help='gpu clock offset value/range, example: [-50:10:150]')
    parser.add_argument("--mem-range", '-m', help='memory clock offset value/range, example: [200:10:300]')
    parser.add_argument("--power-range", '-p', help='power limit value/range, example: [200:10:300]')



    parser.add_argument("--csv", '-s', help='output comma separated data file')
    parser.add_argument("--json", '-j', help='output json file suitable for excavator driver')
    parser.add_argument("--db", '-b', help='output to directory db')

    args = parser.parse_args()

    if args.overclock_file and (args.clock_range or args.power_range or args.mem_range):
        logger.error("Cannot specify boh overlocking file and range")
        sys.exit(0)

    clock_range = parse_range(args.clock_range) if args.clock_range != None else [None]
    power_range = parse_range(args.power_range) if args.power_range != None else [None]
    mem_range = parse_range(args.mem_range) if args.mem_range != None else [None]

    algos = algorithms if args.algorithms == "all" else args.algorithms.split(",")

    if args.overclock_file:
        tasks = generate_tasks_oc_file(args.device, algos, args.length, args.overclock_file)
    else:
        tasks = generate_tasks_oc_range(args.device, algos, args.length, clock_range, power_range, mem_range)

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
            db.save(t.algo, t.dev_uuid, "excavator", "1.5.14a", t.result[0], t.dev_power, t.dev_clock, t.dev_mem, True, t.benchmark_length)

    if(args.csv):
        with open(args.csv, "w") as f:
            f.write(output)

    if(args.json):
        with open(args.json, "w") as f:
            out = json.dumps(top, indent=4, sort_keys=True, separators=(',', ': '))
            logger.debug("Generated json: \n%s", out)
            f.write(out)

