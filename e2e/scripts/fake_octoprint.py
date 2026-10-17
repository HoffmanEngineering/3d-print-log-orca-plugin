#!/usr/bin/env python3
"""A tiny OctoPrint stand-in: just what OrcaSlicer's OctoPrint client calls.

GET  /api/version       -> {"api": "0.1", "server": "1.10.0", "text": "OctoPrint 1.10.0"}
POST /api/files/local   -> saves the multipart "file" part under --dir, answers 201 like OctoPrint

Every request is appended to --log as one JSON line, so the test can count uploads.
"""

import argparse
import email.parser
import email.policy
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--dir", default="/tmp/octoprint")
    parser.add_argument("--log", default="/tmp/octoprint.log")
    args = parser.parse_args()
    os.makedirs(args.dir, exist_ok=True)

    def record(entry):
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            record({"method": "GET", "path": self.path, "api_key": self.headers.get("X-Api-Key")})
            if self.path.startswith("/api/version"):
                self._json(200, {"api": "0.1", "server": "1.10.0", "text": "OctoPrint 1.10.0"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            if not self.path.startswith("/api/files/local"):
                record({"method": "POST", "path": self.path})
                self._json(404, {"error": "not found"})
                return
            raw = (
                b"Content-Type: "
                + self.headers.get("Content-Type", "").encode()
                + b"\r\n\r\n"
                + body
            )
            message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
            fields, filename, size = {}, None, 0
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    filename = part.get_filename()
                    payload = part.get_payload(decode=True) or b""
                    size = len(payload)
                    with open(os.path.join(args.dir, os.path.basename(filename)), "wb") as f:
                        f.write(payload)
                else:
                    fields[name] = part.get_content().strip()
            record(
                {
                    "method": "POST",
                    "path": self.path,
                    "filename": filename,
                    "bytes": size,
                    "fields": fields,
                    "api_key": self.headers.get("X-Api-Key"),
                }
            )
            self._json(
                201,
                {
                    "done": True,
                    "files": {"local": {"name": filename, "origin": "local", "path": filename}},
                },
            )

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
