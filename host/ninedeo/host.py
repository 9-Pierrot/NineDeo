"""Boucle principale de l'hôte natif : lecture des commandes, réponses, évènements."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from . import HOST_NAME, __version__, engine, system
from .config import Config
from .jobs import JobManager
from .protocol import NativeChannel

log = logging.getLogger("ninedeo")


class CommandError(Exception):
    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


def setup_logging(level: int = logging.INFO) -> str:
    logdir = system.app_home() / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "host.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    return str(path)


class Host:
    def __init__(self, channel: NativeChannel) -> None:
        self.channel = channel
        self.config = Config()
        self.ffmpeg = system.find_ffmpeg()
        self.registry = system.FileRegistry()
        self.jobs = JobManager(self.config, self._send_event, self.ffmpeg, self.registry)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cmd")
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "hello": self.cmd_hello,
            "configure": self.cmd_configure,
            "probe": self.cmd_probe,
            "download": self.cmd_download,
            "pause": lambda m: self.jobs.pause(m["job_id"]).public(),
            "resume": lambda m: self.jobs.resume(m["job_id"]).public(),
            "cancel": lambda m: self.jobs.cancel(m["job_id"]).public(),
            "remove": lambda m: self.jobs.remove(m["job_id"]),
            "jobs": lambda m: self.jobs.list(),
            "open": self.cmd_open,
            "reveal": self.cmd_reveal,
            "delete_file": self.cmd_delete_file,
            "choose_folder": self.cmd_choose_folder,
            "update_ytdlp": self.cmd_update_ytdlp,
        }
        # Commandes potentiellement longues : exécutées hors du thread de lecture.
        self.async_cmds = {"probe", "choose_folder", "update_ytdlp", "hello"}

    # ------------------------------------------------------------ transport
    def _send_event(self, event: Dict[str, Any]) -> None:
        self.channel.send(event)

    def _reply(self, msg_id: Any, data: Any = None, error: Optional[CommandError] = None) -> None:
        if error:
            payload = {"id": msg_id, "ok": False,
                       "error": {"code": error.code, "message": error.message, "detail": error.detail}}
        else:
            payload = {"id": msg_id, "ok": True, "data": data}
        try:
            self.channel.send(payload)
        except Exception:
            log.debug("réponse non envoyée (port fermé ?)", exc_info=True)

    def _execute(self, msg: Dict[str, Any]) -> None:
        msg_id = msg.get("id")
        cmd = msg.get("cmd")
        handler = self.handlers.get(cmd)
        if not handler:
            self._reply(msg_id, error=CommandError("unknown_command", f"Commande inconnue : {cmd}"))
            return
        try:
            self._reply(msg_id, handler(msg))
        except CommandError as exc:
            self._reply(msg_id, error=exc)
        except KeyError as exc:
            self._reply(msg_id, error=CommandError("not_found", str(exc).strip("'\"")))
        except Exception as exc:  # noqa: BLE001
            log.exception("commande %s en erreur", cmd)
            code, message, raw = engine.classify_error(exc)
            self._reply(msg_id, error=CommandError(code, message, raw[:1500]))

    def serve(self) -> int:
        log.info("NineDeo hôte %s démarré (yt-dlp %s, ffmpeg=%s)", __version__, engine.ytdlp_version(), self.ffmpeg)
        while True:
            try:
                msg = self.channel.read()
            except Exception:
                log.exception("message illisible")
                break
            if msg is None:
                break
            if not isinstance(msg, dict):
                continue
            if msg.get("cmd") in self.async_cmds:
                self.pool.submit(self._execute, msg)
            else:
                self._execute(msg)

        # Le navigateur a fermé le port : on laisse les téléchargements en cours
        # se terminer (fichiers complets) avant de quitter.
        active = self.jobs.active_count()
        if active:
            log.info("port fermé, %d téléchargement(s) en cours : attente de la fin", active)
            self.jobs.wait_idle()
        log.info("arrêt de l'hôte")
        self.pool.shutdown(wait=False, cancel_futures=True)
        return 0

    # ------------------------------------------------------------- commandes
    def cmd_hello(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        if msg.get("config"):
            self.config.update(msg["config"])
        return {
            "name": HOST_NAME,
            "version": __version__,
            "ytdlp": engine.ytdlp_version(),
            "ffmpeg": self.ffmpeg,
            "ffmpeg_version": system.ffmpeg_version(self.ffmpeg),
            "impersonation": engine.impersonation_available(),
            "default_download_dir": str(system.default_download_dir()),
            "config": self.config.snapshot(),
            "jobs": self.jobs.list(),
            "log_file": str(system.app_home() / "logs" / "host.log"),
            **system.system_info(),
        }

    def cmd_configure(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        self.config.update(msg.get("config") or {})
        with self.jobs._cv:  # la limite de concurrence a pu changer
            self.jobs._cv.notify_all()
        return self.config.snapshot()

    def cmd_probe(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        url = msg.get("url")
        if not url:
            raise CommandError("bad_request", "URL manquante")
        cfg = self.config.snapshot()
        cookie_file = engine.write_cookie_file(msg.get("cookies")) if cfg.get("use_browser_cookies") else None
        try:
            result = engine.probe(url, cfg, msg.get("headers"), cookie_file)
        finally:
            engine.remove_quiet(cookie_file)
        if result.get("drm"):
            raise CommandError("drm", "Ce contenu est protégé par DRM : NineDeo ne prend pas en charge son téléchargement.")
        return result

    def cmd_download(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        spec = msg.get("job") or {}
        try:
            return self.jobs.submit(spec).public()
        except ValueError as exc:
            raise CommandError("bad_request", str(exc))

    def _checked_path(self, msg: Dict[str, Any]) -> str:
        path = msg.get("path")
        if not path:
            raise CommandError("bad_request", "Chemin manquant")
        if not self.registry.allowed(path, self.config.snapshot()["download_dir"]):
            raise CommandError("forbidden_path", "Ce fichier n'a pas été téléchargé par NineDeo.")
        return path

    def cmd_open(self, msg: Dict[str, Any]) -> bool:
        if msg.get("folder"):
            folder = self.config.snapshot()["download_dir"]
            os.makedirs(folder, exist_ok=True)
            system.reveal_path(folder)
            return True
        path = self._checked_path(msg)
        if not os.path.exists(path):
            raise CommandError("missing_file", "Le fichier a été déplacé ou supprimé.")
        system.open_path(path)
        return True

    def cmd_reveal(self, msg: Dict[str, Any]) -> bool:
        path = self._checked_path(msg)
        system.reveal_path(path if os.path.exists(path) else os.path.dirname(path))
        return True

    def cmd_delete_file(self, msg: Dict[str, Any]) -> bool:
        path = self._checked_path(msg)
        if os.path.isfile(path):
            os.remove(path)
        return True

    def cmd_choose_folder(self, msg: Dict[str, Any]) -> Optional[str]:
        try:
            return system.choose_folder(msg.get("initial"))
        except RuntimeError as exc:
            raise CommandError("no_gui", "Sélecteur de dossier indisponible : saisissez le chemin.", str(exc))

    def cmd_update_ytdlp(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        if system.FROZEN:
            raise CommandError(
                "frozen", "Version empaquetée : mettez à jour en réinstallant NineDeo (installeur récent)."
            )
        before = engine.ytdlp_version()
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
             "yt-dlp[default,curl-cffi]"],
            capture_output=True, text=True, timeout=600,
            creationflags=0x08000000 if system.IS_WINDOWS else 0,
        )
        log.info("pip update: rc=%s\n%s\n%s", proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
        if proc.returncode != 0:
            raise CommandError("update_failed", "La mise à jour de yt-dlp a échoué.", proc.stderr[-1500:])
        after = subprocess.run(
            [sys.executable, "-c", "from yt_dlp.version import __version__; print(__version__)"],
            capture_output=True, text=True, timeout=60,
            creationflags=0x08000000 if system.IS_WINDOWS else 0,
        ).stdout.strip() or before
        return {"before": before, "after": after, "restart_required": after != before}


def selftest() -> int:
    """Diagnostic lisible en console : ``ninedeo_host --selftest``."""
    ffmpeg = system.find_ffmpeg()
    report = {
        "host": __version__,
        "yt_dlp": engine.ytdlp_version(),
        "ffmpeg": ffmpeg,
        "ffmpeg_version": system.ffmpeg_version(ffmpeg),
        "impersonation (curl_cffi)": engine.impersonation_available(),
        "download_dir": str(system.default_download_dir()),
        "data_dir": str(system.app_home()),
        **system.system_info(),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ffmpeg else 2


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(__version__)
        return 0
    if "--selftest" in argv:
        return selftest()

    log_path = setup_logging(logging.DEBUG if os.environ.get("NINEDEO_DEBUG") else logging.INFO)
    channel = NativeChannel(sys.stdin.buffer, sys.stdout.buffer)
    # Tout ce qui serait écrit sur stdout corromprait le protocole : on le
    # redirige vers stderr (ou nulle part si stderr n'existe pas).
    sys.stdout = sys.stderr if sys.stderr is not None else open(os.devnull, "w")
    log.info("journal : %s ; arguments : %s", log_path, argv)
    host = Host(channel)
    try:
        return host.serve()
    except KeyboardInterrupt:
        return 0
