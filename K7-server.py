#
# K7-router web UI
#
# Local HTTP server (standard library only) that exposes the routing core as a
# small JSON API and serves the interactive map from static/index.html.
#
# Usage:
#   python K7-server.py            # then open http://127.0.0.1:8707
#   python K7-server.py --port 9000
#

import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import k7core

STATIC_DIR = os.path.join(k7core.BASE_DIR, "static")
MAX_K = 50

# Last computed result, kept so GPX files can be downloaded without recomputing.
_last_result = None
_last_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "K7-router/0.1"

    # --- helpers -----------------------------------------------------------

    def _send(self, status, body, content_type, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _error(self, status, message):
        self._json(status, {"error": message})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # --- routing -----------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path

        if path == "/" or path == "/index.html":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/routes":
            return self._api_routes(query)
        if path.startswith("/api/gpx/"):
            return self._api_gpx(path[len("/api/gpx/"):])
        if path == "/api/health":
            return self._json(200, {"ok": True})
        self._error(404, "Not found")

    def _static(self, rel):
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR)) or not os.path.isfile(full):
            return self._error(404, "Not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _api_routes(self, q):
        global _last_result
        start = (q.get("start") or "").strip()
        end = (q.get("end") or "").strip()
        if not start or not end:
            return self._error(400, "Both 'start' and 'end' are required.")
        try:
            k = max(1, min(MAX_K, int(q.get("k", 7))))
            buffer_m = max(0.0, min(5000.0, float(q.get("buffer", 500))))
        except ValueError:
            return self._error(400, "'k' must be an integer and 'buffer' a number.")
        try:
            result = k7core.find_routes(start, end, k=k, buffer_m=buffer_m)
        except ValueError as exc:
            return self._error(422, str(exc))
        except Exception as exc:  # network errors, Overpass errors, ...
            traceback.print_exc()
            return self._error(502, f"{type(exc).__name__}: {exc}")
        with _last_lock:
            _last_result = result
        self._json(200, result)

    def _api_gpx(self, which):
        with _last_lock:
            result = _last_result
        if result is None:
            return self._error(404, "No routes computed yet.")
        routes = result["routes"]
        if which == "all":
            tracks = [(f"Route {r['rank']} ({r['length_m'] / 1000:.3f} km)", r["coords"]) for r in routes]
            filename = "k7_routes.gpx"
        else:
            try:
                n = int(which)
                route = routes[n - 1]
            except (ValueError, IndexError):
                return self._error(404, "No such route.")
            tracks = [(f"Route {n} ({route['length_m'] / 1000:.3f} km)", route["coords"])]
            filename = f"route_{n}.gpx"
        self._send(200, k7core.build_gpx(tracks), "application/gpx+xml; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="{filename}"'})


def main():
    parser = argparse.ArgumentParser(description="K7-router web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8707)
    parser.add_argument("--verbose", action="store_true", help="print OSMnx log to console")
    args = parser.parse_args()

    k7core.configure(log_console=args.verbose)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"K7-router UI: http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
