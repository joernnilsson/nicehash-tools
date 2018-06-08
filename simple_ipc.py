

import socket
import sys
import time
import threading
import queue
import requests
import json

from http_parser.http import HttpStream
from http_parser.reader import SocketReader

class IpcServer(threading.Thread):

    class Request():
        def __init__(self, cb, socket, path, method, headers, body):
            self.cb = cb
            self.socket = socket
            self.path = path
            self.method = method
            self.header = headers
            self.data = json.loads(body) if len(body) > 0 else None
            self.closed = False
        
        def respond(self, data=None):
            if not self.closed:
                self.closed = True
                d = json.dumps(data) if data else ""
                self.cb(self.socket, d)

    class Empty(queue.Empty):
        pass

    def __init__(self, port=8080):
        threading.Thread.__init__(self)
        self.host = "0.0.0.0"
        self.port = port
        self.requests = queue.Queue(maxsize=50)
        self.running = False

    def get_event(self, block=True, timeout=None):
        try:
            return self.requests.get(block=block, timeout=timeout)
        except queue.Empty:
            raise IpcServer.Empty()

    def run(self):
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            print("Starting ipc server on {host}:{port}".format(host=self.host, port=self.port))
            self.socket.bind((self.host, self.port))

        except Exception as e:
            print("Error: Could not bind to port {port}".format(port=self.port))
            raise e

        try:
            self._listen()
        except:
            if self.running:
                raise

    def shutdown(self):
        print("Shutting down ipc server")
        self.running = False

    def _generate_headers(self, response_code):
        header = ''
        if response_code == 200:
            header += 'HTTP/1.1 200 OK\n'
        else:
            raise NotImplementedError()

        tn = time.strftime("%a, %d %b %Y %H:%M:%S", time.localtime())
        header += 'Date: {now}\n'.format(now=tn)
        header += 'Server: Simple-IPC-Server\n'
        header += 'Connection: close\n\n'
        return header

    def _listen(self):
        self.socket.settimeout(0.1)
        self.socket.listen(5)
        while self.running:
            try:
                (client, address) = self.socket.accept()
                client.settimeout(1.0)
                print("Recieved connection from {addr}".format(addr=address))
                self._handle_client(client, address)
            except socket.timeout:
                continue
        self.socket.shutdown()
        self.socket.close()

    def _respond(self, client, data):
        resp = self._generate_headers(200)
        resp += data
        b = bytearray()
        b.extend(map(ord, resp))
        client.send(b)
        client.close()

    def _handle_client(self, client, address):
        try:
            
            r = SocketReader(client)
            p = HttpStream(r)
            headers = p.headers()
            body = ""
            if "CONTENT-LENGTH" in headers and int(headers["CONTENT-LENGTH"]) > 0:
                body = p.body_string(binary=False)

            # System requests
            if p.path() == "/alive":
                self._respond(client, "ok")
                return

            # User requests
            req = IpcServer.Request(self._respond, client, p.path(), p.method(), headers, body)
            if p.path() == "/message":
                req.respond()

            self.requests.put(req)

        except Exception as e:
            client.close()
            raise e


class IpcClient():
    def __init__(self, address, port):
        self.address = address
        self.port = port
    
    def _url(self, path):
        return 'http://'+self.address+':'+str(self.port)+path

    def is_alive(self):
        try:
            r = requests.post(self._url('/alive'))
            if len(r.text) > 0:
                return True
        except requests.exceptions.ConnectionError:
            return False
        return False

    def message(self, data=None):
        r = requests.post(self._url('/message'), json=data)
    
    def request(self, data=None):
        r = requests.post(self._url('/request'), json=data)
        if len(r.text):
            return r.json()
        else:
            return None

if __name__ == "__main__":

    if len(sys.argv) > 1:

        if sys.argv[1] == "alive":
            client = IpcClient("localhost", 8080)
            print("Server alive: "+str(client.is_alive()))

        if sys.argv[1] == "msg":
            client = IpcClient("localhost", 8080)
            resp = client.message({"batman": 77})
            resp = client.message()
            print("Message sent")

        if sys.argv[1] == "req":
            client = IpcClient("localhost", 8080)
            resp = client.request({"batman": 77})
            print(resp)
        
        sys.exit(0)

    js = IpcServer(8080)
    js.start()
    try:
        while True:
            r = js.requests.get(True)
            print("processing request "+r.path+ "_"+str(r.data)) 
            time.sleep(2.0)
            r.respond({"data": 66})
    except KeyboardInterrupt as e:
        pass

    finally:
        print("Shutting down main")
        js.shutdown()
        print("joining")
        js.join()
