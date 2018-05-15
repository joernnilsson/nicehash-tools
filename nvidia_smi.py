#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import signal
import socket
import sys
import re
import json
import subprocess
from pprint import pprint
from time import sleep

def devices():
    devices = []
    proc = subprocess.run(["nvidia-smi", "-L"], stdout=subprocess.PIPE)
    for line in proc.stdout.decode("utf-8", errors='ignore').splitlines():
        m = re.match(r"GPU ([0-9]+): (.*) [(]UUID: (.*)[)]", line)
        devices.append({
            "id": int(m.group(1)),
            "type": m.group(2),
            "uuid": m.group(3)
        })
    return devices

def device(device_id):
    for d in devices():
        if d["id"] == device_id:
            return d
    return ValueError("Unknown gpu number: %i" % (device_id))

if __name__ == '__main__':
    print(devices())