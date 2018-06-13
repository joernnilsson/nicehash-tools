import threading
import websocket
import websocket_server
import queue
import json
import time


class IpcException(Exception):
    pass

class IpcPacket():
    def __init__(self, cb, client, id, data):
        self.cb = cb
        self.client = client
        self.id = id
        self.data = data
        self.closed = False
        
    def respond(self, data=None):
        if not self.closed:
            self.closed = True
            self.cb(self, data if data else "")


class IpcServer(threading.Thread):

    def __init__(self, port=8080):
        threading.Thread.__init__(self)
        self.host = "0.0.0.0"
        self.port = port
        self.requests = queue.Queue(maxsize=50)
        self.running = False
        self.clients = []

        self.server = websocket_server.WebsocketServer(self.port, self.host)
        self.server.set_fn_new_client(self.connected)
        self.server.set_fn_client_left(self.disconnected)
        self.server.set_fn_message_received(self.received)

        self.next_id = 0

    class Empty(queue.Empty):
        pass

    def get_event(self, block=True, timeout=None):
        try:
            return self.requests.get(block=block, timeout=timeout)
        except queue.Empty:
            raise IpcServer.Empty()

    def __next_id(self):
        self.next_id += 1
        return self.next_id

    def run(self):
        self.server.run_forever()
        print("ipc run done")
    
    def stop(self):
        self.running = False
        self.server.shutdown()
        print("ipc shutdown done")

    def connected(self, client, server):
        print("Got client")
        self.clients.append(client)

    def disconnected(self, client, server):
        self.clients = [x for x in self.clients if x["id"] != client["id"]]

    def publish(self, msg):
        out = json.dumps(
            {
                "type": "message",
                "id": self.__next_id(),
                "data": msg
            }
        )
        self.server.send_message_to_all(out)

    def __respond(self, packet, data={}):
        out = json.dumps(
            {
                "type": "response",
                "id": packet.id,
                "data": data
            }
        )
        self.server.send_message(packet.client, out)

    def received(self, client, server, msg):
        print("Got message/request")
        deserialized = json.loads(msg)
        print(deserialized)
        packet = IpcPacket(self.__respond, client, deserialized["id"], deserialized["data"])

        if deserialized["type"] != "request":
            packet.closed = True

        # Handle internal packets
        if deserialized["type"] == "alive":
            print("Responding to alive")
            self.__respond(packet)
            print("Done responding to alive")
        else:
            self.requests.put(packet)


class IpcClient(threading.Thread):
    def __init__(self, address, port, on_connected=None):
        threading.Thread.__init__(self)
        self.address = address
        self.port = port
        #self.ws = websocket.WebSocket()
        self.requests_out = {}
        self.running = False
        self.messages = queue.Queue(maxsize=50)
        self.on_connected = on_connected

        self.next_id = 0

    class Empty(queue.Empty):
        pass


    def get_event(self, block=True, timeout=None):
        try:
            return self.messages.get(block=block, timeout=timeout)
        except queue.Empty:
            raise IpcClient.Empty()

    def __next_id(self):
        self.next_id += 1
        return self.next_id

    def run(self):
        self.ws = websocket.WebSocketApp("ws://"+self.address+":" + str(self.port) + "/",
            on_message = self.received,
            on_open = self.connected,
            on_error = self.error,
            #on_close = self.closed
            )
        
        self.running = True
        while self.running:
            self.ws.run_forever()
            time.sleep(1)

    def error(self, ws, e):
        print("IPC error "+str(e))

    def closed(self, ws):
        print("IPC Client disconnected")

    def connected(self, ws):
        print("IPC Client connected")
        self.messages.put({"ipc": "connected"})

    def received(self, ws, data):
        print(str(threading.get_ident())+" client received data: "+data)
        parsed = json.loads(data)
        if parsed["type"] == "response" and parsed["id"] in self.requests_out:
            self.requests_out[parsed["id"]][1] = parsed["data"]
            self.requests_out[parsed["id"]][0].set()
        else:
            self.messages.put(parsed["data"])

        
    def stop(self):
        self.running = False
        self.ws.close()
        print("ipc stop done")
        #self.ws.abort()
        #self.ws.close()

    def message(self, msg):
        out = json.dumps(
            {
                "type": "message",
                "id": self.__next_id(),
                "data": msg
            }
        )
        try:
            self.ws.send(out)
        except websocket._exceptions.WebSocketConnectionClosedException:
            print("Ipc client not connected")

    def request(self, data, timeout=None, pct_type="request"):
        seq = self.__next_id()
        out = json.dumps(
            {
                "type": pct_type,
                "id": seq,
                "data": data
            }
        )
        try:
            self.ws.send(out)
        except websocket._exceptions.WebSocketConnectionClosedException:
            raise IpcException("Ipc client not connected")

        # Block until received
        ev = threading.Event()
        self.requests_out[seq] = [ev, None]
        print(str(threading.get_ident())+" waiting for event "+str(seq))
        o = ev.wait(timeout=timeout)
        print(str(threading.get_ident())+" o "+str(o))
        print(str(threading.get_ident())+" deleting event "+str(seq))
        p = self.requests_out[seq]
        del self.requests_out[seq]
        if o:
            out = p[1]
            return out
        else:
            raise IpcException("Timeout waiting for request response")

    def is_alive(self):
        try:
            print(str(threading.get_ident())+" Før alive wait")
            resp = self.request({}, pct_type="alive", timeout=1.0)
            print(str(threading.get_ident())+" Etter alive wait")
        except websocket._exceptions.WebSocketConnectionClosedException:
            return False
        return True

if __name__ == "__main__":

    port = 8080

    server = IpcServer(port)
    client = IpcClient("localhost", port)

    def reply_in():
        time.sleep(4)
        req = server.requests.get()
        req.respond("Batman")

    try:
        server.start()
        client.start()
        time.sleep(1)

        t = threading.Thread(target=reply_in)
        t.start()

        resp = client.request("Heisann")
        print("Client got response: "+ resp)
    except Exception as e:
        print("exp:" + str(e))
        raise e

    finally:
        #client.stop()
        server.stop()