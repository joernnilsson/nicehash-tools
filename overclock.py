import requests, time, os, sys
import logging
import http.cookiejar
import imaplib
import re
import json
import tempfile
import subprocess
import xml.etree.ElementTree as ET

class Device:

    def __init__(self, number):
        self.logger = logging.getLogger(__name__)
        self.device_number = number


    def set_power(self, limit):
        limit_default = self.get("gpu/power_readings/default_power_limit", tp = "W")
        limit_min = self.get("gpu/power_readings/min_power_limit", tp = "W")
        limit_max = self.get("gpu/power_readings/max_power_limit", tp = "W")


        self.logger.info("[GPU-%d] Setting power limit to: %f W", self.device_number, limit)

        if(limit < limit_min or limit > limit_max):
            raise Exception('Tried to set power limit to %f, while min/max is: %f/%f' % (limit, limit_min, limit_max))
        
        self.nvidia_smi(["-pl", str(limit)])
        #print(proc.returncode)

    def set_clock_offset(self, offset):
        proc = self.nvidia_settings(["-a", '[gpu:0]/GPUPowerMizerMode=1'])
        proc = self.nvidia_settings(["-a", '[gpu:0]/GPUGraphicsClockOffset[2]='+str(int(offset))])

    def get_clock_offset(self):
        offset = int(self.nvidia_settigns_get('GPUGraphicsClockOffset[2]'))
        return offset

    def get_temp(self):
        return self.get("gpu/temperature/gpu_temp", tp = "C")

    def get_power_limit(self):
        return self.get("gpu/power_readings/power_limit", tp = "W")

    def nvidia_settigns_get(self, var):
        proc = self.nvidia_settings(["-t", '-q', '[gpu:' + str(self.device_number) + ']/' + var])
        return proc.stdout.decode("utf-8", errors='ignore')

    def nvidia_settings(self, args):
        cmd = ["nvidia-settings"] + args
        self.logger.debug("Running: %s", cmd)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.logger.debug("nvidia-settings: '%s'", proc.stdout.decode("utf-8", errors='ignore'))

        if(proc.returncode != 0):
            raise Exception("nvidia-settings exited with error %d" % (proc.returncode))
        
        return proc    

    def nvidia_smi(self, args):
        cmd = ["nvidia-smi", "-i", str(self.device_number)] + args
        self.logger.debug("Running: %s", cmd)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE)
        #print(proc.returncode)
        #print(proc.stdout.decode("utf-8", errors='ignore'))

        if(proc.returncode != 0):
            raise Exception("nvidia-smi exied with error %d" % (proc.returncode))

        if(proc.stdout.decode("utf-8", errors='ignore').find("not supported for GPU") != -1):
            raise Exception("nvidia-smi exied with error: Opeartion not supported for GPU")
        
        return proc

    def refresh(self):
        self.data = self.query()
        #for a in data.find("./gpu"):
        #    print(a)

    def get(self, key, tp = None):
        val = self.data.find(key).text
        if(val == "N/A"):
            raise Exception("Key %s is not available" % (key))

        if(tp == "W"):
            m = re.match(r"([0-9]*\.[0-9]+|[0-9]+) W", val)
            return float(m.group(1))

        elif(tp == "C"):
            m = re.match(r"([0-9]*\.[0-9]+|[0-9]+) C", val)
            return float(m.group(1))

        else:
            return val

    def query(self):
        proc = self.nvidia_smi(["-q", "-x"])
        with open("out.xml", "w") as f:
            f.write(proc.stdout.decode("utf-8", errors='ignore') )
        return ET.fromstring(proc.stdout.decode("utf-8") )
        #return ET.parse("1080.xml")





if(__name__ == "__main__"):
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(filename)-20.20s:%(lineno)4d] [%(levelname)-5.5s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ])

    import argparse
    parser = argparse.ArgumentParser(description='Set overclocking parameters for a given GPU')

    parser.add_argument("--device", "-d", help='device number (default: 0)', default = 0, type=int)
    parser.add_argument("--clock", "-c", help='set clock offset [Hz]', type=int)
    parser.add_argument("--power", "-p", help='set the power limit [W]', type=float)

    args = parser.parse_args()

    dev = Device(args.device)
    dev.refresh()

    if(args.power):
        dev.set_power(args.power)

    if(args.clock):
        dev.set_clock_offset(args.clock)

    print(str(dev.get_clock_offset()))
