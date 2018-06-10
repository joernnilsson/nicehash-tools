
import sys
import time
import os
import re
import threading
import serial
import signal
import queue

import nvidia_smi

class Sensor():
    def __init__(self, key, name=None):
        self.key = key
        self.name = name
        if name == None:
            self.name = key
        self.value = 0.0
    
    def set(self, val):
        self.value = val
        print(self.name+": "+str(self.value))

    def get(self):
        return self.value

class Source():
    pass

class SerialSource(Source):
    pass

class NicehasSource(Source):
    pass

class ExcavatorDriverSource(Source):
    pass


class MinerMonitor():
    def __init__(self):

        self.sensors = {}
        self.add_sensor(Sensor("t_water_hot", "Coolant hot"))
        self.add_sensor(Sensor("t_water_cold", "Coolant cold"))

        self.queue = queue.Queue()

        self.running = True

        self.serial_port = None
        #self.ws_server = WsServer(9090, self.process_line)

    def add_sensor(self, sensor):
        self.sensors[sensor.key] = sensor

    def smi(self):
        devices = nvidia_smi.devices()
        for d in devices:
            self.add_sensor(Sensor("t_gpu."+str(d["id"]), "Gpu "+str(d["id"])))

        rate = 1
        while self.running:
            for d in devices:
                temp = nvidia_smi.temperature(d["id"])
                self.queue.put(("t_gpu."+str(d["id"]), temp))
            time.sleep(1.0/rate)


    def serial(self):
        while self.running:
            try:
                with serial.Serial(sys.argv[1], 115200, timeout=0.1) as ser:
                    self.serial_port = ser
                    while self.running:
                        line = ser.readline().strip()
                        if(len(line) > 0):
                            self.process_line(line.decode('utf-8', 'ignore'))
                self.serial_port = None

            except serial.serialutil.SerialException as e:
                print("Serial connect error: "+str(e))
                pass

            if self.running:
                i = 0
                while self.running and i < 20:
                    time.sleep(0.1)
                    i += 1
                print("Reconnectiong serial port")

    def stop(self):
        print("Stopping")
        self.running = False
        #self.ws_server.stop()

    def join(self):
        print("Joining")
        self.thread_smi.join()
        self.thread_serial.join()
        #self.ws_server.join()

    def run(self):

        self.thread_serial = threading.Thread(target=self.serial)
        #self.thread_serial.start()
        
        self.thread_smi = threading.Thread(target=self.smi)
        #self.thread_smi.start()

        #self.ws_server.start()

        while self.running:
            try:
                e = self.queue.get(block=True, timeout=0.1)
            except queue.Empty:
                continue
            self.input(e[0], e[1])
        

    def input(self, key, value):

        key_parts = key.split(".")

        if key_parts[0] == "cmd":
            print("Got command: "+key+": "+value)
            self.input("t_water_hot", 23.0)

        else:
            if key == "log":
                print("FW Log: "+value)

            elif key == "sys.state":
                print("FW State: "+value)

            elif key in self.sensors:
                self.sensors[key].set(float(value))

            else:
                print("ERROR unknown command: "+key)
            
            self.ws_server.publish(key+"/"+str(value))


    def process_line(self, line):
        m = re.match("(.*)/(.*)", str(line))
        if m != None:
            #print(line)
            self.queue.put((m.group(1), m.group(2)))
        else:
            print("ERROR__ parsing: "+line)

    def publish(self, line):
        self.ws_server.publish(line)

    def lcd_print(self, line, text):
        if self.serial_port:
            enc = "lcd." + str(line) + "/" + text + "\n"
            self.serial_port.write(enc.encode())

if __name__ == "__main__":

    monitor = MinerMonitor()

    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
        monitor.join()