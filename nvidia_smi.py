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
import queue
import time
import threading

from pprint import pprint
from time import sleep

class Monitor(threading.Thread):
    def __init__(self, device_ids=None, data=["xidEvent","temp","procClk","pwrDraw","memClk","violPwr","violThm"]):
        threading.Thread.__init__(self)
        self.running = True
        self.queue = queue.Queue(maxsize=50)
        self.device_ids = device_ids
        self.data = data
        self.daemon = True
        self.proc = None

    def stop(self):
        self.running = False
        if self.proc:
            self.proc.terminate()

    def run(self):
        cmd = ["nvidia-smi", "stats", "-d", ",".join(self.data)]
        if self.device_ids:
            cmd += [str(d) for d in self.device_ids]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        while(self.running):
            line = self.proc.stdout.readline().decode("utf-8", errors='ignore')
            if not self.running or len(line) == 0:
                continue
            parts = line.split(",")
            event = {
                "id": int(parts[0]),
                "type": parts[1].strip(),
                "time": int(parts[2]),
                "value": int(parts[3])
            }
            self.queue.put(event)

    def get_event(self, block=True, timeout=None):
        try:
            return self.queue.get(block=block, timeout=timeout)
        except queue.Empty:
            raise Monitor.Empty()

    class Empty(queue.Empty):
        pass

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

def temperature(device_id):
    proc = subprocess.run(["nvidia-smi", "dmon", "-i", str(device_id), "-c", "1", "-s", "p"], stdout=subprocess.PIPE)
    lines = proc.stdout.decode("utf-8", errors='ignore').splitlines()
    m = re.match("\s+([0-9.-]+)\s+([0-9.-]+)\s+([0-9.-]+)", lines[2])
    return float(m.group(3))

if __name__ == '__main__':
    print(devices())
    m = Monitor()
    m.start()
    while(True):
        print(m.queue.get(True))
