#!/usr/bin/python3

import sys
import re
import os
import time
import tempfile
import subprocess


def xorg():
    
    config_filename = tempfile.mktemp()
    print(sys.argv) 

    subprocess.run(["nvidia-xconfig", "-a", "--allow-empty-initial-configuration", "--cool-bits=31", "--use-display-device='DFP-0'", "--connected-monitor='DFP-0'", "-o", config_filename])
    print("Using config file: " + config_filename)
    
    proc = subprocess.Popen(["xinit", sys.argv[0], "spin", "--", ":0", "-once", "-config", config_filename])
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait(timeout=10.0)
        proc.kill()
        proc.wait(timeout=5.0)


if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "spin":
        while True:
            print("spin...")
            time.sleep(1.0)
    else:
        xorg()
