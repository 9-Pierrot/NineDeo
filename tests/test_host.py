"""Tests de l'hôte natif NineDeo.

  * tests unitaires (préréglages, cookies, classification d'erreurs) ;
  * tests d'intégration : l'hôte est lancé comme le ferait le navigateur et
    piloté via le protocole Native Messaging contre le serveur local.

Prérequis : ``bash tests/make_fixtures.sh`` puis ``python -m pytest tests``.
"""
from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tests"))

from ninedeo import engine  # noqa: E402
from ninedeo.config import Config  # noqa: E402
from server import make_server  # noqa: E402

MEDIA = ROOT / "tests" / "media"
pytestmark_media = pytest.mark.skipif(not (MEDIA / "sample.mp4").exists(), reason="lancer make_fixtures.sh")


# ---------------------------------------------------------------- unitaires
def test_format_selector_presets():
    assert engine.format_selector("best", None, True) == "bv*+ba/b"
    assert engine.format_selector("best", None, False) == "b"
    assert engine.format_selector("h720", None, True) == "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
    assert engine.format_selector("audio_m4a", None, True) == "ba[ext=m4a]/ba/b"
    assert engine.format_selector("best", "137", True) == "137+ba/137"


def test_cookie_file_roundtrip(tmp_path):
    path = engine.write_cookie_file([
        {"name": "session", "value": "ok", "domain": "127.0.0.1", "hostOnly": True, "path": "/", "session": True},
        {"name": "pref", "value": "x", "domain": "example.com", "hostOnly": False, "path": "/",
         "secure": True, "expirationDate": 2000000000.5},
        {"name": "bad\tname", "value": "v", "domain": "example.com"},
    ])
    try:
        text = Path(path).read_text("utf-8")
        assert "127.0.0.1\tFALSE\t/\tFALSE\t0\tsession\tok" in text
        assert ".example.com\tTRUE\t/\tTRUE\t2000000000\tpref\tx" in text
        assert "bad" not in text
        from yt_dlp.cookies import YoutubeDLCookieJar

        jar = YoutubeDLCookieJar(path)
        jar.load()
        assert {c.name for c in jar} == {"session", "pref"}
    finally:
        engine.remove_quiet(path)


@pytest.mark.parametrize("msg,code", [
    ("ERROR: Unsupported URL: https://x", "unsupported"),
    ("ERROR: [generic] This video is DRM protected", "drm"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "forbidden"),
    ("ERROR: Sign in to confirm your age", "auth_required"),
    ("something odd", "error"),
])
def test_classify_error(msg, code):
    assert engine.classify_error(Exception(msg))[0] == code


def test_config_validation():
    cfg = Config()
    snap = cfg.update({"max_concurrent": 99, "container": "avi", "embed_metadata": 0, "unknown": 1})
    assert snap["max_concurrent"] == 8
    assert snap["container"] == "mp4"
    assert snap["embed_metadata"] is False
    assert "unknown" not in snap


def test_literal_template_escapes_percent():
    assert engine.literal_template("100% Live / Test") == "100%% Live ⧸ Test.%(ext)s"


# ------------------------------------------------------------ intégration
class NativeClient:
    """Imite le navigateur : lance l'hôte et dialogue en Native Messaging."""

    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "host" / "ninedeo_host.py"), "chrome-extension://test/"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.replies = {}
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.jobs = {}
        self._cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        out = self.proc.stdout
        while True:
            head = out.read(4)
            if len(head) < 4:
                return
            (n,) = struct.unpack("=I", head)
            msg = json.loads(out.read(n).decode("utf-8"))
            with self._cv:
                if "id" in msg and "ok" in msg:
                    self.replies[msg["id"]] = msg
                elif msg.get("event") == "job":
                    self.jobs[msg["job"]["id"]] = msg["job"]
                    self.events.put(msg)
                self._cv.notify_all()

    def send(self, cmd, timeout=60, **kw):
        mid = uuid.uuid4().hex
        data = json.dumps({"id": mid, "cmd": cmd, **kw}).encode()
        self.proc.stdin.write(struct.pack("=I", len(data)) + data)
        self.proc.stdin.flush()
        with self._cv:
            if not self._cv.wait_for(lambda: mid in self.replies, timeout=timeout):
                raise TimeoutError(cmd)
            return self.replies.pop(mid)

    def wait_job(self, job_id, predicate, timeout=60):
        with self._cv:
            ok = self._cv.wait_for(lambda: job_id in self.jobs and predicate(self.jobs[job_id]), timeout=timeout)
            assert ok, f"état attendu non atteint : {self.jobs.get(job_id)}"
            return dict(self.jobs[job_id])

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()


@pytest.fixture(scope="module")
def server():
    srv = make_server(0, rate_kib=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def slow_server():
    srv = make_server(0, rate_kib=400)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def client(tmp_path):
    env = dict(os.environ, NINEDEO_HOME=str(tmp_path / "home"))
    c = NativeClient(env)
    hello = c.send("hello", config={"download_dir": str(tmp_path / "out"), "impersonate": False})
    assert hello["ok"], hello
    c.outdir = tmp_path / "out"
    yield c
    c.close()


def _job(url, **kw):
    return {"id": uuid.uuid4().hex, "url": url, **kw}


@pytestmark_media
def test_hello_reports_versions(client):
    r = client.send("hello")
    assert r["data"]["name"] == "com.ninedeo.host"
    assert r["data"]["ffmpeg"]
    assert r["data"]["config"]["download_dir"] == str(client.outdir)


@pytestmark_media
def test_probe_hls_master(client, server):
    r = client.send("probe", url=f"{server}/media/hls/master.m3u8")
    assert r["ok"], r
    assert r["data"]["heights"] == [720, 360]


@pytestmark_media
def test_probe_unsupported_page(client, server):
    r = client.send("probe", url=f"{server}/empty.html")
    assert not r["ok"]
    assert r["error"]["code"] == "unsupported"


@pytestmark_media
def test_download_direct_mp4_with_title(client, server):
    job = _job(f"{server}/media/sample.mp4", kind="media", title="Ma vidéo : test")
    assert client.send("download", job=job)["ok"]
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"))
    assert j["status"] == "finished", j
    assert j["filename"] == "Ma vidéo - test.mp4" or j["filename"].startswith("Ma vidéo")
    assert Path(j["filepath"]).stat().st_size > 100_000


@pytestmark_media
def test_download_hls_720p_preset(client, server):
    job = _job(f"{server}/media/hls/master.m3u8", kind="media", title="hls-720", preset="h720")
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"), timeout=120)
    assert j["status"] == "finished", j
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=height", "-of", "csv=p=0", j["filepath"]], capture_output=True, text=True)
    assert probe.stdout.strip() == "720"


@pytestmark_media
def test_download_hls_aes128(client, server):
    job = _job(f"{server}/media/hls_aes/index.m3u8", kind="media", title="hls-aes")
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"), timeout=120)
    assert j["status"] == "finished", j


@pytestmark_media
def test_download_dash(client, server):
    job = _job(f"{server}/media/dash/manifest.mpd", kind="media", title="dash")
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"), timeout=120)
    assert j["status"] == "finished", j
    assert j["filepath"].endswith(".mp4")


@pytestmark_media
def test_audio_mp3_preset(client, server):
    job = _job(f"{server}/media/sample.webm", kind="media", title="son", preset="audio_mp3")
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"), timeout=120)
    assert j["status"] == "finished", j
    assert j["filepath"].endswith(".mp3")


@pytestmark_media
def test_cookies_are_forwarded(client, server):
    url = f"{server}/protected/sample.mp4"
    job = _job(url, kind="media", title="sans-cookie")
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"))
    assert j["status"] == "error" and j["error"]["code"] == "forbidden"

    cookie = {"name": "session", "value": "ok", "domain": "127.0.0.1", "hostOnly": True, "path": "/", "session": True}
    job = _job(url, kind="media", title="avec-cookie", cookies=[cookie])
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"))
    assert j["status"] == "finished", j


@pytestmark_media
def test_referer_header_forwarded(client, server):
    job = _job(f"{server}/referer/sample.mp4", kind="media", title="referer",
               headers={"Referer": f"{server}/player.html"})
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"))
    assert j["status"] == "finished", j


@pytestmark_media
def test_fallback_chain_page_then_stream(client, server):
    # La page de test n'est pas reconnue par yt-dlp -> repli sur le flux détecté.
    job = _job(f"{server}/empty.html", kind="page", title="repli",
               fallbacks=[{"url": f"{server}/media/sample.mp4", "kind": "media", "title": "repli"}])
    client.send("download", job=job)
    j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"))
    assert j["status"] == "finished", j
    assert j["used_url"].endswith("sample.mp4")
    assert j["warnings"]


@pytestmark_media
def test_pause_resume_and_cancel(client, slow_server):
    job = _job(f"{slow_server}/media/sample.mp4", kind="media", title="lent")
    client.send("download", job=job)
    client.wait_job(job["id"], lambda j: j["status"] == "downloading" and (j["downloaded"] or 0) > 200_000)
    client.send("pause", job_id=job["id"])
    j = client.wait_job(job["id"], lambda j: j["status"] == "paused")
    partial = list(client.outdir.glob("lent.mp4.part"))
    assert partial, "le fichier partiel doit être conservé pendant la pause"
    size_at_pause = partial[0].stat().st_size

    client.send("resume", job_id=job["id"])
    j = client.wait_job(job["id"], lambda j: j["status"] == "downloading" and (j["downloaded"] or 0) > size_at_pause)
    client.send("cancel", job_id=job["id"])
    j = client.wait_job(job["id"], lambda j: j["status"] == "canceled")
    time.sleep(0.5)
    assert not list(client.outdir.glob("lent.mp4*")), "les fichiers partiels doivent être supprimés"


@pytestmark_media
def test_open_refuses_foreign_paths(client):
    r = client.send("open", path=str(ROOT / "README.md"))
    assert not r["ok"] and r["error"]["code"] == "forbidden_path"


@pytestmark_media
def test_same_title_gets_unique_name(client, server):
    names = []
    for _ in range(2):
        job = _job(f"{server}/media/sample.mp4", kind="media", title="Doublon")
        client.send("download", job=job)
        j = client.wait_job(job["id"], lambda j: j["status"] in ("finished", "error"))
        assert j["status"] == "finished", j
        names.append(j["filename"])
    assert names == ["Doublon.mp4", "Doublon (2).mp4"]


def test_hls_and_dash_manifest_analysis_rules():
    """Même règles DRM que l'extension (background.js) : SAMPLE-AES = DRM, AES-128 = non."""
    import re
    drm_hls = re.compile(r'#EXT-X-(SESSION-)?KEY:[^\n]*(METHOD=SAMPLE-AES(-CTR|-CENC)?|KEYFORMAT="(com\.apple\.streamingkeydelivery|urn:uuid:[0-9a-f-]+|com\.microsoft\.playready)")', re.I)
    assert drm_hls.search('#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://x",KEYFORMAT="com.apple.streamingkeydelivery"')
    assert not drm_hls.search('#EXT-X-KEY:METHOD=AES-128,URI="enc.key"')
