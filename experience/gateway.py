"""Public-facing gateway forwarding ONLY Meta's webhook to localhost HOLD.

The downstream HOLD server performs the actual HMAC signature verification.
The whole experience UI and mission API remain bound to 127.0.0.1:8787.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

WEBHOOK = "/api/whatsapp/webhook"
MAX_BODY = 256 * 1024


def handler_for(upstream: str = "http://127.0.0.1:8787"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HOLDWebhookGateway/0.2"
        sys_version = ""

        def log_message(self, fmt, *args):
            # Do not print hub.verify_token or webhook message payload.
            print(f"gateway {self.command} {urlsplit(self.path).path}", flush=True)

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            if urlsplit(self.path).path != WEBHOOK:
                self.send_error(404, "Only the signed Meta webhook is public")
                return
            data = None
            headers = {}
            if self.command == "POST":
                if self.headers.get("Transfer-Encoding"):
                    self.send_error(400, "Chunked requests not supported")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    size = -1
                if size < 0 or size > MAX_BODY:
                    self.send_error(413, "Invalid payload size")
                    return
                data = self.rfile.read(size)
                if len(data) != size:
                    self.send_error(400, "Incomplete body")
                    return
                headers = {"Content-Type": "application/json"}
                if self.headers.get("X-Hub-Signature-256"):
                    headers["X-Hub-Signature-256"] = self.headers["X-Hub-Signature-256"]
            req = Request(upstream + self.path, data=data, headers=headers, method=self.command)
            try:
                with urlopen(req, timeout=10) as res:
                    code, content, ct = res.status, res.read(MAX_BODY), res.headers.get("Content-Type", "text/plain")
            except HTTPError as ex:
                code, content, ct = ex.code, ex.read(MAX_BODY), ex.headers.get("Content-Type", "text/plain")
            except (URLError, TimeoutError):
                self.send_error(502, "Local HOLD server unavailable")
                return
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Expose only the signed WhatsApp webhook")
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for())
    print(f"Webhook gateway: http://127.0.0.1:{args.port}{WEBHOOK} (only this path)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
