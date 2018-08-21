import json
import logging
import signal
import socket
import sys
import re
import json
import subprocess
import time
from pprint import pprint
from time import sleep

import logging
import os


class BenchmarkDb():
    def __init__(self, directory):
        self.directory = directory

        if not os.path.exists(directory):
            os.makedirs(directory)

        self.logger = logging.getLogger()
        self.logger.debug("Using db: %s", self.directory)

    def _filepath(self, algo, gpu_uuid, miner, miner_version):
        filename = "%s_%s_%s_%s.csv" % (algo, miner, miner_version, gpu_uuid)
        filepath = self.directory + "/" + filename
        return filepath

    def save(self, algo, gpu_uuid, miner, miner_version, hashrate, power_offset, gpu_clock_offset, mem_clock_offset, success):
        
        filepath = self._filepath(algo, gpu_uuid, miner, miner_version)
        with open(filepath, "a+") as fd:
            csv = ", ".join([str(x) for x in [1 if success else 0, hashrate, power_offset, gpu_clock_offset, mem_clock_offset]])
            self.logger.debug("Writing: %s", csv)
            fd.write(csv + "\n")



