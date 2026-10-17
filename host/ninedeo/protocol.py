"""Couche de transport Native Messaging.

Chaque message est un objet JSON encodé en UTF-8, précédé de sa longueur
sur 4 octets dans l'ordre natif de la machine (little-endian sur x86).
Le navigateur limite les messages hôte -> extension à 1 Mo.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import threading
from typing import Any, BinaryIO, Optional

# Limite imposée par Chrome/Firefox pour les messages envoyés par l'hôte.
MAX_OUTGOING = 1024 * 1024
# Garde-fou côté lecture (le navigateur autorise jusqu'à 64 Mo).
MAX_INCOMING = 64 * 1024 * 1024


class ProtocolError(Exception):
    pass


def _set_binary_mode(stream: BinaryIO) -> None:
    """Sous Windows, s'assure qu'aucune conversion CRLF n'est appliquée."""
    if sys.platform == "win32":
        try:
            import msvcrt

            msvcrt.setmode(stream.fileno(), os.O_BINARY)
        except Exception:  # pragma: no cover - dépend de l'environnement
            pass


class NativeChannel:
    def __init__(self, inp: BinaryIO, out: BinaryIO):
        _set_binary_mode(inp)
        _set_binary_mode(out)
        self._in = inp
        self._out = out
        self._lock = threading.Lock()

    def _read_exact(self, n: int) -> Optional[bytes]:
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._in.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read(self) -> Optional[Any]:
        """Lit un message. Renvoie None quand le navigateur ferme le port."""
        header = self._read_exact(4)
        if header is None:
            return None
        (length,) = struct.unpack("=I", header)
        if length > MAX_INCOMING:
            raise ProtocolError(f"message trop volumineux ({length} octets)")
        payload = self._read_exact(length)
        if payload is None:
            return None
        return json.loads(payload.decode("utf-8"))

    @staticmethod
    def encode(obj: Any) -> bytes:
        return json.dumps(
            obj, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")

    def send(self, obj: Any) -> None:
        data = self.encode(obj)
        if len(data) > MAX_OUTGOING:
            # On ne doit jamais dépasser la limite : le navigateur couperait
            # la connexion. On renvoie une erreur exploitable à la place.
            fallback = {
                "id": obj.get("id") if isinstance(obj, dict) else None,
                "ok": False,
                "error": {
                    "code": "too_large",
                    "message": "Réponse trop volumineuse pour le navigateur.",
                },
            }
            data = self.encode(fallback)
        with self._lock:
            self._out.write(struct.pack("=I", len(data)))
            self._out.write(data)
            self._out.flush()
