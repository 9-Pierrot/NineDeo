#!/usr/bin/env python3
"""Serveur de test local pour NineDeo.

Sert ``tests/media`` (généré par make_fixtures.sh) et ``tests/pages`` avec :
  * prise en charge des requêtes ``Range`` (reprise de téléchargement) ;
  * limitation de débit optionnelle (--rate, en Kio/s) pour tester pause/reprise ;
  * /protected/...  : exige le cookie ``session=ok`` (test des cookies navigateur) ;
  * /referer/...    : exige un en-tête Referer provenant de ce serveur ;
  * /login          : dépose le cookie ``session=ok``.

Usage : python tests/server.py --port 8765 [--rate 300]
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import time
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("application/dash+xml", ".mpd")
mimetypes.add_type("video/mp2t", ".ts")
mimetypes.add_type("video/iso.segment", ".m4s")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("text/javascript", ".js")


class Handler(SimpleHTTPRequestHandler):
    rate_kib = 0
    extra_dirs = {}

    def log_message(self, fmt, *args):  # silencieux sauf erreurs
        if os.environ.get("NINEDEO_SERVER_VERBOSE"):
            super().log_message(fmt, *args)

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, base in self.extra_dirs.items():
            if clean.startswith(prefix):
                return str(Path(base) / clean[len(prefix):].lstrip("/"))
        for prefix in ("/protected/", "/referer/"):
            if clean.startswith(prefix):
                clean = "/media/" + clean[len(prefix):]
        if clean.startswith("/media/"):
            return str(ROOT / "media" / clean[len("/media/"):])
        rel = clean.lstrip("/") or "index.html"
        return str(ROOT / "pages" / rel)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _guard(self) -> bool:
        if self.path.startswith("/protected/") and "session=ok" not in (self.headers.get("Cookie") or ""):
            self.send_error(HTTPStatus.FORBIDDEN, "cookie session manquant")
            return False
        if self.path.startswith("/referer/"):
            ref = self.headers.get("Referer") or ""
            if not ref.startswith(f"http://{self.headers.get('Host')}"):
                self.send_error(HTTPStatus.FORBIDDEN, "referer invalide")
                return False
        return True

    def do_GET(self):
        if self.path.startswith("/login"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Set-Cookie", "session=ok; Path=/")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>Connecté (cookie session=ok déposé).</p>".encode())
            return
        if not self._guard():
            return
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().do_GET()
        size = os.path.getsize(path)
        ctype = self.guess_type(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = HTTPStatus.OK
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            chunk = 16 * 1024
            while remaining > 0:
                data = fh.read(min(chunk, remaining))
                if not data:
                    break
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(data)
                if self.rate_kib:
                    time.sleep(len(data) / (self.rate_kib * 1024))


def make_server(port: int, rate_kib: int = 0, extra_dirs=None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"rate_kib": rate_kib, "extra_dirs": dict(extra_dirs or {})})
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(handler, directory=str(ROOT)))
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--rate", type=int, default=0, help="débit max en Kio/s (0 = illimité)")
    ap.add_argument("--vendor", action="append", default=[], help="/prefixe=dossier servi en plus")
    args = ap.parse_args()
    extra = dict(v.split("=", 1) for v in args.vendor)
    srv = make_server(args.port, args.rate, extra)
    print(f"NineDeo test server: http://127.0.0.1:{args.port}/ (rate={args.rate or '∞'} KiB/s)")
    srv.serve_forever()
