import sys
import re
import os
import time
import tempfile
import subprocess


def xorg():
    
    config_filename = tempfile.mktemp()
   

    subprocess.run(["nvidia-xconfig", "-a", "--allow-empty-initial-configuration", "--cool-bits=31", "--use-display-device=\"DFP-0\"", "--connected-monitor=\"DFP-0\"", "-o", config_filename])
    print("Using config file: " + config_filename)
    subprocess.run(["xinit", os.getcwd() + "/" + sys.argv[0], "--", ":0", "-once", "-config", config_filename])

if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "spin":
        while True:
            print("spin...")
            time.sleep(1.0)
    else:
        xorg()