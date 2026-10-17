"""Réglages de l'hôte. La source de vérité est l'extension (chrome.storage) :
elle envoie la commande ``configure`` à chaque connexion et à chaque modification.
"""
from __future__ import annotations

import threading
from typing import Any, Dict

from .system import default_download_dir

DEFAULTS: Dict[str, Any] = {
    "download_dir": None,            # None => ~/Downloads/NineDeo
    "filename_template": "%(title).180B.%(ext)s",
    "default_preset": "best",
    "container": "mp4",              # mp4 | mkv
    "prefer_compatible": True,       # préférer H.264/AAC à qualité égale
    "audio_format": "mp3",
    "audio_quality": "192",          # kb/s pour le MP3
    "max_concurrent": 3,
    "concurrent_fragments": 4,
    "rate_limit_kib": 0,             # 0 = illimité
    "allow_playlists": False,
    "embed_metadata": True,
    "embed_thumbnail": False,
    "embed_subtitles": False,
    "subtitle_langs": "fr,en",
    "use_browser_cookies": True,
    "impersonate": True,             # imitation TLS navigateur si curl_cffi est présent
}

_INT_KEYS = {"max_concurrent": (1, 8), "concurrent_fragments": (1, 16), "rate_limit_kib": (0, 10_000_000)}


class Config:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = dict(DEFAULTS)

    def update(self, values: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in (values or {}).items():
                if key not in DEFAULTS:
                    continue
                if key in _INT_KEYS:
                    lo, hi = _INT_KEYS[key]
                    try:
                        value = max(lo, min(hi, int(value)))
                    except (TypeError, ValueError):
                        continue
                elif isinstance(DEFAULTS[key], bool):
                    value = bool(value)
                elif key == "download_dir":
                    value = (str(value).strip() or None) if value else None
                elif key == "container" and value not in ("mp4", "mkv"):
                    continue
                elif key == "audio_format" and value not in ("mp3", "m4a", "opus", "flac", "wav"):
                    continue
                self._data[key] = value
            return dict(self._data)

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            data = dict(self._data)
        data["download_dir"] = data["download_dir"] or str(default_download_dir())
        return data
