import http.server
import socketserver
import os
import threading


class StaticHttpServer(threading.Thread):
    def __init__(self, port, path):
        threading.Thread.__init__(self)
        self.port = port
        self.path = path
        self.httpd = None
    
    def run(self):

        web_dir = os.path.join(os.path.dirname(__file__), self.path)
        os.chdir(web_dir)

        Handler = http.server.SimpleHTTPRequestHandler
        self.httpd = socketserver.TCPServer(("", self.port), Handler)
        self.httpd.serve_forever()

    def stop(self):
        self.httpd.shutdown()

if __name__ == "__main__":
    server = StaticHttpServer(8080, "web")
    server.run()