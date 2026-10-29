"""CI-only echo target, reachable exclusively on the disposable Docker network."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if self.path != '/tick' or not 0 < size < 4096:
            self.send_error(400)
            return
        body = json.loads(self.rfile.read(size))
        result = json.dumps({'method': 'POST', 'path': self.path, 'body': body}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, *args):
        pass


HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
