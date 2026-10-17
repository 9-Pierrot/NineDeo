"""Fonctions dépendantes du système : chemins, ffmpeg, explorateur de fichiers."""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("ninedeo.system")

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))

# Extensions que l'hôte accepte d'« ouvrir » avec l'application par défaut.
# Tout le reste est seulement affiché dans l'explorateur : on n'exécute jamais
# un fichier arbitraire à la demande de l'extension.
OPENABLE_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".3gp",
    ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac",
    ".srt", ".vtt", ".jpg", ".jpeg", ".png", ".webp",
}


def app_home() -> Path:
    """Dossier de données de NineDeo (journaux, historique des fichiers)."""
    override = os.environ.get("NINEDEO_HOME")
    if override:
        base = Path(override)
    elif IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "NineDeo"
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support" / "NineDeo"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ninedeo"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    if IS_WINDOWS:
        # Respecte un dossier « Téléchargements » déplacé par l'utilisateur.
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            )
            value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            if value:
                downloads = Path(os.path.expandvars(value))
        except Exception:
            pass
    return downloads / "NineDeo"


def _host_dir() -> Path:
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_ffmpeg() -> Optional[str]:
    """Cherche ffmpeg : variable d'env., dossier bin/ de l'hôte, puis PATH."""
    exe = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    candidates = []
    env = os.environ.get("NINEDEO_FFMPEG")
    if env:
        candidates.append(Path(env))
    candidates += [_host_dir() / "bin" / exe, app_home() / "bin" / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffmpeg")
    return found


def ffmpeg_version(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=10,
            creationflags=_no_window_flag(),
        ).stdout
        first = out.splitlines()[0] if out else ""
        parts = first.split()
        return parts[2] if len(parts) > 2 else first
    except Exception:
        return None


def _no_window_flag() -> int:
    return 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


def system_info() -> dict:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "python": platform.python_version(),
        "frozen": FROZEN,
    }


# --------------------------------------------------------------------------
# Registre des fichiers produits (pour autoriser « ouvrir » / « supprimer »)
# --------------------------------------------------------------------------
class FileRegistry:
    LIMIT = 5000

    def __init__(self) -> None:
        self._path = app_home() / "files.json"
        self._lock = threading.Lock()
        try:
            self._files = list(json.loads(self._path.read_text("utf-8")))
        except Exception:
            self._files = []

    def add(self, path: str) -> None:
        norm = os.path.normcase(os.path.abspath(path))
        with self._lock:
            if norm in self._files:
                return
            self._files.append(norm)
            self._files = self._files[-self.LIMIT:]
            try:
                self._path.write_text(json.dumps(self._files), "utf-8")
            except Exception:
                log.exception("impossible d'écrire le registre des fichiers")

    def allowed(self, path: str, download_dir: Optional[str]) -> bool:
        norm = os.path.normcase(os.path.abspath(path))
        with self._lock:
            if norm in self._files:
                return True
        if download_dir:
            base = os.path.normcase(os.path.abspath(download_dir))
            try:
                return os.path.commonpath([norm, base]) == base
            except ValueError:  # lecteurs différents sous Windows
                return False
        return False


def open_path(path: str) -> None:
    """Ouvre un fichier média avec l'application par défaut."""
    p = Path(path)
    if p.is_file() and p.suffix.lower() not in OPENABLE_EXTENSIONS:
        reveal_path(path)
        return
    if IS_WINDOWS:
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal_path(path: str) -> None:
    """Affiche le fichier sélectionné dans l'explorateur."""
    p = Path(path)
    if IS_WINDOWS:
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p if p.exists() else p.parent))  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    else:
        target = p.parent if p.is_file() else p
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def choose_folder(initial: Optional[str]) -> Optional[str]:
    """Ouvre une boîte de dialogue native de sélection de dossier (Tk)."""
    result: dict = {}

    def run() -> None:
        try:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(
                parent=root,
                initialdir=initial or str(default_download_dir()),
                title="NineDeo — choisir le dossier de téléchargement",
                mustexist=False,
            )
            root.destroy()
            result["path"] = chosen or None
        except Exception as exc:  # pragma: no cover - interface graphique
            result["error"] = str(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=600)
    if "error" in result:
        raise RuntimeError(result["error"])
    path = result.get("path")
    return os.path.normpath(path) if path else None
