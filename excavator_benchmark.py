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
    json_data = json.dumps(config)
    fd, config_file = tempfile.mkstemp()
    logger.debug("Writing temp config to: %s", config_file)
    with os.fdopen(fd, 'w') as tmp:
        tmp.write(json_data)

    print(["excavator", "-d", str(device), "-c", config_file])
    proc = subprocess.run(["excavator", "-d", str(device), "-c", config_file], stdout=subprocess.PIPE)

    m = re.search("total speed: ([0-9]*\.[0-9]+|[0-9]+)\s([a-zA-Z])?H/s", str(proc.stdout))
    if(m == None):
        raise Exception("Could not extract hashrate from excavator output")
    
    rate = get_rate(float(m.group(1)), m.group(2))

    os.remove(config_file)

    return rate


def get_rate(rate, unit):
    if(unit == None):
        mul = 1
    elif(unit == "k"):
        mul = 1e3
    elif(unit == "M"):
        mul = 1e6
    elif(unit == "G"):
        mul = 1e9
    else:
        raise Exception("Unknown unit multiplier: "+unit)
    return rate * mul

def make_config(algo, device, benchmark_length):

    # Genearate config
    return [
        {
            "time": 0,
            "commands": [
            {
                "id": 1,
                "method": "algorithm.add",
                "params": [
                algo,
                "benchmark",
                ""
                ]
            }
            ]
        },
        {
            "time": 3,
            "commands": [
            {
                "id": 1,
                "method": "worker.add",
                "params": [
                "0",
                str(device),
                "M=1"
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
    rate = benchmark(args.excavator_path, args.device, args.algo, args.length)
    logger.info("Benchmark finished, %s rate: %f H/s", args.algo, rate)
