import requests, time, os, sys
import logging
import http.cookiejar
import imaplib
import re
import json
import tempfile
import subprocess


def benchmark(executable, device, algo, benchmark_length):

    logger = logging.getLogger(__name__)


    config = make_config(algo, device, benchmark_length)
    json_data = json.dumps(config, indent=4)
    fd, config_file = tempfile.mkstemp()
    logger.debug("Writing temp config to: %s", config_file)
    with os.fdopen(fd, 'w') as tmp:
        tmp.write(json_data)

    #print(["excavator", "-d", str(device), "-c", config_file])
    #print(json_data)
    #proc = subprocess.run(["excavator", "-d", str(6), "-c", config_file])
    #sys.exit(0)
    proc = subprocess.run(["excavator", "-d", str(6), "-c", config_file], stdout=subprocess.PIPE)

    # Handle dual algos
    rates = []

    ms = re.findall("speed: ([0-9]*\.[0-9]+|[0-9]+)\s([a-zA-Z])?H/s", str(proc.stdout))
    for m in ms:
        #print(proc.stdout.decode("utf-8", errors='ignore'))
        rate = get_rate(float(m[0]), m[1])
        rates.append(rate)

    if(len(ms) == 0):
        print(json_data)
        print(proc.stdout.decode("utf-8", errors='ignore'))
        raise Exception("Could not extract hashrate from excavator output")

    os.remove(config_file)

    return rates


def get_rate(rate, unit):
    if(unit == None or len(unit) == 0):
        mul = 1
    elif(unit == "k"):
        mul = 1e3
    elif(unit == "M"):
        mul = 1e6
    elif(unit == "G"):
        mul = 1e9
    else:
        raise Exception("Unknown unit multiplier: '"+unit+"'")
    return rate * mul

def make_config(algo, device, benchmark_length):

    algo_commands = []
    for a in algo.split("_"):
        algo_commands.append({
                "id": 1,
                "method": "algorithm.add",
                "params": [
                a,
                "benchmark"
                ]
            })

    # Genearate config
    return [
        {
            "time": 0,
            "commands": algo_commands
        },
        {
            "time": 3,
            "commands": [
            {
                "id": 1,
                "method": "worker.add",
                "params": [
                algo,
                str(device)
                ]
            }
            ]
        },
        {
            "time": benchmark_length + 3,
            "commands": [
            {
                "id": 1,
                "method": "algorithm.print.speeds",
                "params": [
                "0"
                ]
            },
            {
                "id": 1,
                "method": "quit",
                "params": [
                
                ]
            }
            ]
        }
    ]


if(__name__ == "__main__"):

    import argparse
    parser = argparse.ArgumentParser(description='Run a single excavator benchmark')

    parser.add_argument("--algo", '-a', help='algorithm', required = True)
    parser.add_argument("--device", "-d", help='device number (default: 0)', default = 0, type=int)
    parser.add_argument("--length", '-l', help='length [s] (default: 100)', default = 100, type=int)
    parser.add_argument("--excavator-path", '-e', help='path to excavator executable (default: excavator)', default="excavator")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])
    logger = logging.getLogger(__name__)


    logger.info("Running %s benchmark for %d seconds", args.algo, args.length)
    rates = benchmark(args.excavator_path, args.device, args.algo, args.length)
    logger.info("Benchmark finished, %s rate: [%s] H/s", args.algo, ", ".join(str(x) for x in rates))
