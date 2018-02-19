#!/usr/bin/python3

# Requirements:
# sudo apt-get install libcap-dev
# pip3 install --user python-prctl

import os
import sys
import subprocess
import re
import time
import signal
import prctl

# global process handle
process = None

def get_temperature_nvidia():
    cmd = ["nvidia-smi", "dmon", "-s", "p", "-c", "1"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE)
    text = proc.stdout.decode("utf-8", errors='ignore')
    max_temp = -100
    for line in text.splitlines():
        m = re.match(r"^\s+([0-9]+)\s+([0-9\-]+)\s+([0-9\-]+)", line)
        if(m):
            gpu = m.group(1)
            pwr = m.group(2)
            temp = int(m.group(3))
            if(temp > max_temp):
                max_temp = temp
    if(max_temp == -100):
        print(text)
        raise Exception("Could not read temperature")
    return max_temp

def run(max_allowed, args):
    global process

    temp = get_temperature_nvidia()
    
    if(temp > max_allowed):
        print("ERROR: Temperature above max limit, not staring (temp, max: %d, %d)" % (temp, max_allowed))
        return

    try:
        # Prctl ensures the subprocess gets KILL signal when super exits
        process = subprocess.Popen(args, preexec_fn=lambda: prctl.set_pdeathsig(signal.SIGKILL))

        while True:
            temp = get_temperature_nvidia()

            if(temp > max_allowed):      
                print("ERROR: Temperature above max limit, killing process(temp, max: %d, %d)" % (temp, max_allowed))
                kill(process)

            try:
                process.wait(timeout=1.0)
                return process.returncode
            except subprocess.TimeoutExpired as e:
                pass
    except KeyboardInterrupt as ki:
        kill(process, True)

    except Exception as e:
        print("ERROR: Got exception from main loop, killing process")
        kill(process)
        raise(e)

def kill(proc, silent = False):
    if(proc):
        proc.kill()
        proc.wait()
        if(not silent):
            print("Process killed")

def signal_handler(signum, frame):
    global process
    print("ERROR: Got signal " + str(signum) + ", killing process")
    kill(process)

if(__name__ == "__main__"):

    if(len(sys.argv) < 3):
        print("usage: "+sys.argv[0]+ " [max temperature] [commmand ...]")
        sys.exit(1)

    max_allowed = int(sys.argv[1])
    command = sys.argv[2:]
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    
    run(max_allowed, command)

