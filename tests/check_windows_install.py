#!/usr/bin/env python3
"""Vérifie une installation Windows réelle du moteur NineDeo (utilisé par la CI).

1. Les clés de registre Chrome / Edge / Firefox pointent vers un manifeste valide.
2. L'hôte est lancé exactement comme le fait Chrome (cmd.exe /d /c "<path>" <origine>)
   et répond en Native Messaging.
3. Un téléchargement HLS réel (serveur local) aboutit, fusion ffmpeg comprise.

Usage : python tests/check_windows_install.py
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from server import make_server  # noqa: E402

HOST = "com.ninedeo.host"
EXT_ID = "hcijkjkgdbihledmefkjiafcejmcgjom"
KEYS = {
    "Chrome": rf"Software\Google\Chrome\NativeMessagingHosts\{HOST}",
    "Edge": rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST}",
    "Chromium": rf"Software\Chromium\NativeMessagingHosts\{HOST}",
    "Firefox": rf"Software\Mozilla\NativeMessagingHosts\{HOST}",
}


def ok(msg):
    print("  OK ", msg, flush=True)


def main() -> int:
    manifests = {}
    for name, key in KEYS.items():
        path = winreg.QueryValue(winreg.HKEY_CURRENT_USER, key)
        data = json.loads(Path(path).read_text("utf-8"))
        assert data["name"] == HOST, data
        assert Path(data["path"]).is_file(), data["path"]
        if name == "Firefox":
            assert data["allowed_extensions"] == ["ninedeo@ninedeo.app"], data
        else:
            assert data["allowed_origins"] == [f"chrome-extension://{EXT_ID}/"], data
        manifests[name] = data
        ok(f"registre {name} -> {path}")

    host_path = manifests["Chrome"]["path"]
    if host_path.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd.exe", "/d", "/c", host_path, f"chrome-extension://{EXT_ID}/"]
    else:
        cmd = [host_path, f"chrome-extension://{EXT_ID}/"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def send(obj):
        data = json.dumps(obj).encode()
        proc.stdin.write(struct.pack("=I", len(data)) + data)
        proc.stdin.flush()

    def read():
        head = proc.stdout.read(4)
        if len(head) < 4:
            raise RuntimeError("hôte fermé : " + proc.stderr.read().decode(errors="replace"))
        (n,) = struct.unpack("=I", head)
        return json.loads(proc.stdout.read(n).decode("utf-8"))

    out_dir = Path(tempfile.mkdtemp(prefix="ninedeo-ci-"))
    send({"id": "1", "cmd": "hello", "config": {"download_dir": str(out_dir)}})
    hello = read()
    assert hello["ok"], hello
    info = hello["data"]
    assert info["ffmpeg"], "ffmpeg introuvable"
    ok(f"hôte {info['version']} · yt-dlp {info['ytdlp']} · ffmpeg {info['ffmpeg_version']} · impersonation={info['impersonation']}")

    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    job_id = uuid.uuid4().hex
    send({"id": "2", "cmd": "download", "job": {
        "id": job_id, "url": f"{base}/media/hls/master.m3u8", "kind": "media",
        "title": "Test Windows é à ü", "preset": "h720"}})
    deadline = time.time() + 180
    final = None
    while time.time() < deadline:
        msg = read()
        if msg.get("event") == "job" and msg["job"]["id"] == job_id and msg["job"]["status"] in ("finished", "error"):
            final = msg["job"]
            break
    assert final and final["status"] == "finished", final
    size = Path(final["filepath"]).stat().st_size
    ok(f"téléchargement HLS 720p : {final['filename']} ({size} octets)")

    proc.stdin.close()
    proc.wait(timeout=30)
    srv.shutdown()
    print("INSTALLATION WINDOWS VALIDÉE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
