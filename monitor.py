
import sys
import time
import os
import re
import threading
import serial
import signal
import queue
from websocket_server import WebsocketServer
import ws_ipc
import json

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

class WsServer(threading.Thread):

    def __init__(self, port, msg_cb=None):
        threading.Thread.__init__(self)

        self.clients = []
        self.msg_cb = msg_cb

        self.server = WebsocketServer(port, "0.0.0.0")
        self.server.set_fn_new_client(self.connected)
        self.server.set_fn_client_left(self.disconnected)
        self.server.set_fn_message_received(self.received)

    def run(self):
        self.server.run_forever()
    
    def stop(self):
        self.server.shutdown()

    def connected(self, client, server):
        self.clients.append(client)

    def disconnected(self, client, server):
        self.clients = [x for x in self.clients if x["id"] != client["id"]]

    def publish(self, msg):
        self.server.send_message_to_all(json.dumps(msg))

    def received(self, client, server, msg):
        if self.msg_cb:
            self.msg_cb(json.loads(msg))

class MinerMonitor():
    def __init__(self):

        self.sensors = {}
        self.add_sensor(Sensor("t_water_hot", "Coolant hot"))
        self.add_sensor(Sensor("t_water_cold", "Coolant cold"))
        self.add_sensor(Sensor("t_air_ambient_1", "Air 1"))
        self.add_sensor(Sensor("t_air_ambient_2", "Air 2"))

        self.queue = queue.Queue()

        self.running = True

        self.serial_port = None
        self.ws_server = WsServer(9090, self.process_json)

        self.excavator_driver = ws_ipc.IpcClient("localhost", 8082)

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
        if len(sys.argv) < 2:
            return
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
        self.ws_server.stop()
        self.excavator_driver.stop()

    def join(self):
        print("Joining")
        self.thread_smi.join()
        print("b")
        self.thread_serial.join()
        print("c")
        self.excavator_driver.join()
        print("d")
        self.ws_server.join()
        print("e")

    def run(self):

        self.thread_serial = threading.Thread(target=self.serial)
        self.thread_serial.start()
        
        self.thread_smi = threading.Thread(target=self.smi)
        self.thread_smi.start()

        self.ws_server.start()

        print(str(threading.get_ident())+" monitor run")
        self.excavator_driver.start()

        while self.running:
         
            try:
                e = self.excavator_driver.get_event(block=False)
                if "ipc" in e and e["ipc"] == "connected":
                    self.excavator_driver.message({"cmd": "publish.state"})
                else:
                    self.ws_server.publish(e)
            except ws_ipc.IpcClient.Empty:
                pass

            try:
                e = self.queue.get(block=True, timeout=0.1)
            except queue.Empty:
                continue
            self.input(e[0], e[1])
        

    def input(self, key, value):

        key_parts = key.split(".")

        if key_parts[0] == "cmd":
            print("Got command: "+key+": "+value)
            #self.input("t_water_hot", 23.0)

        else:
            if key == "log":
                print("FW Log: "+value)

            elif key == "sys.state":
                print("FW State: "+value)

            elif key in self.sensors:
                self.sensors[key].set(float(value))

            else:
                print("ERROR unknown command: "+key)
            
            #self.ws_server.publish(key+"/"+str(value))
            self.ws_server.publish({"param": key, "value": value})

    def process_json(self, data):
        print("Got json: "+str(data))
        print("Got json: "+str(type(data)))
        if "cmd" in data:
            self.excavator_driver.message(data)

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
