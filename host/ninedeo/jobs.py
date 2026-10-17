"""File d'attente et exécution des téléchargements (un thread par tâche active)."""
from __future__ import annotations

import glob
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Set

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from . import engine
from .config import Config
from .system import FileRegistry

log = logging.getLogger("ninedeo.jobs")

ACTIVE = {"queued", "starting", "downloading", "processing"}
_INTERMEDIATE = re.compile(r"\.f[0-9A-Za-z_-]+\.[0-9A-Za-z]{2,4}$")
EMIT_INTERVAL = 0.25  # s entre deux mises à jour de progression

PP_LABELS = {
    "Merger": "Fusion audio + vidéo",
    "FFmpegMerger": "Fusion audio + vidéo",
    "ExtractAudio": "Conversion audio",
    "FFmpegExtractAudio": "Conversion audio",
    "FixupM3u8": "Correction du flux",
    "FFmpegFixupM3u8": "Correction du flux",
    "FixupM4a": "Correction du conteneur",
    "FFmpegFixupM4a": "Correction du conteneur",
    "FixupStretched": "Correction du ratio",
    "FixupDuplicateMoov": "Correction du conteneur",
    "FixupTimestamp": "Correction des horodatages",
    "Metadata": "Écriture des métadonnées",
    "FFmpegMetadata": "Écriture des métadonnées",
    "EmbedThumbnail": "Ajout de la miniature",
    "ThumbnailsConvertor": "Conversion de la miniature",
    "FFmpegThumbnailsConvertor": "Conversion de la miniature",
    "EmbedSubtitle": "Ajout des sous-titres",
    "FFmpegEmbedSubtitle": "Ajout des sous-titres",
    "MoveFiles": "Finalisation",
    "MoveFilesAfterDownload": "Finalisation",
}


class _Pause(DownloadCancelled):
    msg = "Mis en pause"


class _Cancel(DownloadCancelled):
    msg = "Annulé"


@dataclass
class Job:
    id: str
    url: str
    kind: str = "page"                      # page | media
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    page_url: Optional[str] = None
    preset: str = "best"
    format_id: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: List[dict] = field(default_factory=list)
    fallbacks: List[dict] = field(default_factory=list)
    playlist: Optional[bool] = None
    output_base: Optional[str] = None       # nom de fichier retenu pour un flux direct
    reuse_name: bool = False

    status: str = "queued"
    stage: Optional[str] = None
    progress: Optional[float] = None
    downloaded: int = 0
    total: Optional[int] = None
    speed: Optional[float] = None
    eta: Optional[int] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    error: Optional[dict] = None
    warnings: List[str] = field(default_factory=list)
    playlist_index: Optional[int] = None
    playlist_count: Optional[int] = None
    used_url: Optional[str] = None
    extractor: Optional[str] = None
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None

    # interne
    _stop: Optional[str] = None             # None | "pause" | "cancel"
    _tmpfiles: Set[str] = field(default_factory=set)
    _finalfiles: Set[str] = field(default_factory=set)
    _last_emit: float = 0.0

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id, "url": self.url, "kind": self.kind, "title": self.title,
            "thumbnail": self.thumbnail, "page_url": self.page_url, "preset": self.preset,
            "format_id": self.format_id, "status": self.status, "stage": self.stage,
            "progress": self.progress, "downloaded": self.downloaded, "total": self.total,
            "speed": self.speed, "eta": self.eta, "filename": self.filename,
            "filepath": self.filepath, "error": self.error, "warnings": self.warnings[-5:],
            "playlist_index": self.playlist_index, "playlist_count": self.playlist_count,
            "used_url": self.used_url, "extractor": self.extractor, "created": self.created,
            "started": self.started, "finished": self.finished, "output_base": self.output_base,
        }

    @classmethod
    def from_spec(cls, spec: Dict[str, Any]) -> "Job":
        if not spec.get("id") or not spec.get("url"):
            raise ValueError("id et url sont obligatoires")
        return cls(
            id=str(spec["id"]),
            url=str(spec["url"]),
            kind=spec.get("kind") or "page",
            title=spec.get("title"),
            thumbnail=spec.get("thumbnail"),
            page_url=spec.get("page_url"),
            preset=spec.get("preset") or "best",
            format_id=spec.get("format_id"),
            headers=dict(spec.get("headers") or {}),
            cookies=list(spec.get("cookies") or []),
            fallbacks=list(spec.get("fallbacks") or []),
            playlist=spec.get("playlist"),
            output_base=spec.get("title") if spec.get("reuse_name") else None,
            reuse_name=bool(spec.get("reuse_name")),
        )


class JobManager:
    def __init__(self, config: Config, emit: Callable[[Dict[str, Any]], None],
                 ffmpeg: Optional[str], registry: FileRegistry) -> None:
        self.config = config
        self.emit = emit
        self.ffmpeg = ffmpeg
        self.registry = registry
        self.jobs: Dict[str, Job] = {}
        self._pending: Deque[str] = deque()
        self._running: Set[str] = set()
        self._cv = threading.Condition()
        self._closing = False
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="dispatcher", daemon=True)
        self._dispatcher.start()

    # ------------------------------------------------------------------ API
    def submit(self, spec: Dict[str, Any]) -> Job:
        job = Job.from_spec(spec)
        with self._cv:
            existing = self.jobs.get(job.id)
            if existing and existing.status in ACTIVE:
                return existing
            self.jobs[job.id] = job
            self._pending.append(job.id)
            self._cv.notify_all()
        self._emit(job, force=True)
        return job

    def pause(self, job_id: str) -> Job:
        return self._stop(job_id, "pause")

    def cancel(self, job_id: str) -> Job:
        return self._stop(job_id, "cancel")

    def resume(self, job_id: str) -> Job:
        with self._cv:
            job = self._get(job_id)
            if job.status in ACTIVE:
                return job
            job.status, job.error, job.stage, job._stop = "queued", None, None, None
            job.speed = job.eta = None
            job.finished = None
            self._pending.append(job.id)
            self._cv.notify_all()
        self._emit(job, force=True)
        return job

    def remove(self, job_id: str) -> None:
        with self._cv:
            job = self.jobs.get(job_id)
            if not job:
                return
            if job.status in ACTIVE:
                job._stop = "cancel"
                if job.id in self._pending:
                    self._pending.remove(job.id)
            else:
                self.jobs.pop(job_id, None)

    def list(self) -> List[Dict[str, Any]]:
        with self._cv:
            return [j.public() for j in self.jobs.values()]

    def active_count(self) -> int:
        with self._cv:
            return sum(1 for j in self.jobs.values() if j.status in ACTIVE)

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.time() + timeout
        with self._cv:
            while self._pending or self._running:
                remaining = None if deadline is None else deadline - time.time()
                if remaining is not None and remaining <= 0:
                    return False
                self._cv.wait(timeout=remaining if remaining is not None else 1.0)
        return True

    def shutdown(self) -> None:
        with self._cv:
            self._closing = True
            for job in self.jobs.values():
                if job.status in ACTIVE:
                    job._stop = "pause"
            self._cv.notify_all()

    # ------------------------------------------------------------ internals
    def _get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(f"tâche inconnue : {job_id}")
        return job

    def _stop(self, job_id: str, mode: str) -> Job:
        with self._cv:
            job = self._get(job_id)
            if job.id in self._pending:
                self._pending.remove(job.id)
                job.status = "paused" if mode == "pause" else "canceled"
                job.finished = time.time()
                job._stop = None
                emit_now = True
            elif job.status in ACTIVE:
                job._stop = mode
                emit_now = False
            else:
                if mode == "cancel" and job.status == "paused":
                    job.status = "canceled"
                    self._cleanup_partials(job)
                emit_now = True
        if emit_now:
            self._emit(job, force=True)
        return job

    def _dispatch_loop(self) -> None:
        while True:
            with self._cv:
                while not self._closing and (
                    not self._pending or len(self._running) >= int(self.config.get("max_concurrent") or 3)
                ):
                    self._cv.wait()
                if self._closing:
                    return
                job_id = self._pending.popleft()
                job = self.jobs.get(job_id)
                if not job:
                    continue
                self._running.add(job_id)
                job.status = "starting"
                job.started = time.time()
            threading.Thread(target=self._run, args=(job,), name=f"job-{job_id[:8]}", daemon=True).start()

    def _emit(self, job: Job, force: bool = False) -> None:
        now = time.time()
        if not force and now - job._last_emit < EMIT_INTERVAL:
            return
        job._last_emit = now
        try:
            self.emit({"event": "job", "job": job.public()})
        except Exception:  # le port peut être fermé
            log.debug("émission impossible", exc_info=True)

    # ----------------------------------------------------------- exécution
    def _run(self, job: Job) -> None:
        candidates = [{"url": job.url, "kind": job.kind, "title": job.title, "headers": job.headers}]
        for fb in job.fallbacks:
            if isinstance(fb, dict) and fb.get("url") and fb["url"] != job.url:
                candidates.append(fb)
        try:
            last_exc: Optional[BaseException] = None
            for index, cand in enumerate(candidates):
                try:
                    self._download(job, cand)
                    last_exc = None
                    break
                except DownloadCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if job._stop:
                        raise DownloadCancelled() from exc
                    code, _, raw = engine.classify_error(exc)
                    last_exc = exc
                    has_next = index + 1 < len(candidates)
                    log.warning("[%s] échec sur %s (%s) : %s", job.id[:8], cand["url"], code, raw)
                    if has_next and code in {"unsupported", "not_found", "forbidden", "format_unavailable", "error"}:
                        job.warnings.append(f"Source {index + 1} ignorée : {raw[:200]}")
                        self._reset_progress(job)
                        continue
                    raise
            if last_exc:
                raise last_exc
            job.status = "finished"
            job.progress = 1.0
            job.stage = None
            job.speed = job.eta = None
        except DownloadCancelled:
            if job._stop == "pause":
                job.status = "paused"
            else:
                job.status = "canceled"
                self._cleanup_partials(job)
            job.speed = job.eta = None
            job.stage = None
        except Exception as exc:  # noqa: BLE001
            code, message, raw = engine.classify_error(exc)
            job.status = "error"
            job.error = {"code": code, "message": message, "detail": raw[:1500]}
            job.stage = None
            job.speed = job.eta = None
            log.exception("[%s] tâche en erreur", job.id[:8])
        finally:
            job.finished = time.time()
            job._stop = None
            with self._cv:
                self._running.discard(job.id)
                removed = job.id not in self.jobs
                self._cv.notify_all()
            if not removed:
                self._emit(job, force=True)

    @staticmethod
    def _reset_progress(job: Job) -> None:
        job.progress = None
        job.downloaded = 0
        job.total = None
        job.speed = job.eta = None
        job.stage = None

    def _download(self, job: Job, cand: Dict[str, Any]) -> None:
        cfg = self.config.snapshot()
        url = cand["url"]
        is_media = cand.get("kind") == "media"
        headers = dict(job.headers)
        headers.update(cand.get("headers") or {})
        cookie_file = engine.write_cookie_file(job.cookies) if cfg.get("use_browser_cookies") else None
        title_override = None
        if is_media:
            # Nom unique choisi une seule fois : une reprise réutilise le même
            # fichier .part, deux vidéos de même titre ne s'écrasent pas.
            if not job.output_base:
                job.output_base = engine.unique_base(cfg["download_dir"], cand.get("title") or job.title or "video")
            title_override = job.output_base
        try:
            opts = engine.build_options(
                cfg,
                preset=job.preset,
                format_id=job.format_id if cand is not None and url == job.url else None,
                headers=headers,
                cookie_file=cookie_file,
                ffmpeg=self.ffmpeg,
                title_override=title_override,
                playlist=job.playlist,
            )
            opts["logger"] = engine.YdlLogger(f"[{job.id[:8]}] ", on_warning=lambda m: self._warn(job, m))
            opts["progress_hooks"] = [lambda d: self._on_progress(job, d)]
            opts["postprocessor_hooks"] = [lambda d: self._on_postprocess(job, d)]
            opts["post_hooks"] = [lambda path: self._on_final(job, path)]

            job.used_url = url
            job.status = "downloading"
            job.stage = "Analyse de la page" if not is_media else "Connexion au flux"
            self._emit(job, force=True)
            log.info("[%s] début %s (preset=%s, format=%s)", job.id[:8], url, job.preset, opts["format"])

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info:
                job.extractor = info.get("extractor_key") or info.get("extractor")
                if not job.title or not is_media:
                    job.title = info.get("title") or job.title
                job.thumbnail = job.thumbnail or info.get("thumbnail")
                # Fichier déjà présent : yt-dlp ne déclenche pas les hooks
                for rd in info.get("requested_downloads") or []:
                    if rd.get("filepath") and os.path.exists(rd["filepath"]):
                        self._on_final(job, rd["filepath"])
            if job._stop:
                raise DownloadCancelled()
        finally:
            engine.remove_quiet(cookie_file)

    # --------------------------------------------------------------- hooks
    def _warn(self, job: Job, msg: str) -> None:
        msg = str(msg)
        if "ffmpeg" in msg.lower() or "drm" in msg.lower():
            job.warnings.append(msg[:300])

    def _check_stop(self, job: Job) -> None:
        if job._stop == "pause":
            raise _Pause()
        if job._stop == "cancel":
            raise _Cancel()

    def _on_progress(self, job: Job, d: Dict[str, Any]) -> None:
        self._check_stop(job)
        info = d.get("info_dict") or {}
        for key in ("tmpfilename", "filename"):
            if d.get(key):
                job._tmpfiles.add(d[key])
        if info.get("playlist_index"):
            job.playlist_index = info.get("playlist_index")
            job.playlist_count = info.get("n_entries") or info.get("playlist_count")
        if info.get("title") and (job.kind != "media" or not job.title):
            job.title = info.get("title")
        if not job.thumbnail and info.get("thumbnail"):
            job.thumbnail = info.get("thumbnail")

        status = d.get("status")
        if status == "downloading":
            job.status = "downloading"
            downloaded = int(d.get("downloaded_bytes") or 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            frac = None
            if total:
                frac = min(1.0, downloaded / float(total))
            elif d.get("fragment_count"):
                frac = min(1.0, (d.get("fragment_index") or 0) / float(d["fragment_count"]))

            # Vidéo + audio téléchargés l'un après l'autre : progression globale
            requested = info.get("requested_formats") or []
            fid = info.get("format_id")
            idx = next((i for i, f in enumerate(requested) if f.get("format_id") == fid), None)
            if requested and idx is not None and len(requested) > 1:
                sizes = [f.get("filesize") or f.get("filesize_approx") for f in requested]
                if all(sizes):
                    done = sum(sizes[:idx])
                    whole = float(sum(sizes))
                    job.total = int(whole)
                    job.downloaded = int(done + downloaded)
                    job.progress = min(1.0, (done + (frac or 0) * sizes[idx]) / whole) if frac is not None else None
                else:
                    job.progress = (idx + (frac or 0)) / len(requested) if frac is not None else None
                    job.downloaded = downloaded
                    job.total = int(total) if total else None
                kind = "Vidéo" if (requested[idx].get("vcodec") or "none") != "none" else "Audio"
                job.stage = f"{kind} ({idx + 1}/{len(requested)})"
            else:
                job.progress = frac
                job.downloaded = downloaded
                job.total = int(total) if total else None
                job.stage = None
            if job.playlist_index and job.playlist_count:
                job.stage = f"Élément {job.playlist_index}/{job.playlist_count}" + (f" · {job.stage}" if job.stage else "")
            job.speed = d.get("speed")
            job.eta = int(d["eta"]) if d.get("eta") is not None else None
            job.filename = os.path.basename(d.get("filename") or "") or job.filename
            self._emit(job)
        elif status == "finished":
            job.filename = os.path.basename(d.get("filename") or "") or job.filename
            self._emit(job, force=True)

    def _on_postprocess(self, job: Job, d: Dict[str, Any]) -> None:
        if d.get("status") == "started":
            name = d.get("postprocessor") or ""
            label = PP_LABELS.get(name, "Traitement")
            job.status = "processing"
            job.stage = label
            job.speed = job.eta = None
            self._emit(job, force=True)
        elif d.get("status") == "finished":
            path = (d.get("info_dict") or {}).get("filepath")
            if path:
                job.filename = os.path.basename(path)

    def _on_final(self, job: Job, path: str) -> None:
        if not path:
            return
        path = os.path.abspath(path)
        job.filepath = path
        job.filename = os.path.basename(path)
        job._finalfiles.add(path)
        self.registry.add(path)
        try:
            size = os.path.getsize(path)
            job.total = job.total or size
            if job.status != "downloading":
                job.downloaded = max(job.downloaded, size)
        except OSError:
            pass

    def _cleanup_partials(self, job: Job) -> None:
        """Supprime les fichiers partiels d'une tâche annulée."""
        for path in list(job._tmpfiles):
            if path in job._finalfiles:
                continue
            candidates = {path, path + ".part", path + ".ytdl"}
            if path.endswith(".part"):
                base = path[: -len(".part")]
                candidates |= {base + ".ytdl"}
            candidates |= set(glob.glob(glob.escape(path) + "-Frag*"))
            candidates |= set(glob.glob(glob.escape(path) + ".part-Frag*"))
            for c in candidates:
                if c in job._finalfiles:
                    continue
                # .part / .ytdl / fragments, et pistes intermédiaires « titre.f137.mp4 »
                if c.endswith((".part", ".ytdl")) or "-Frag" in c or _INTERMEDIATE.search(c):
                    engine.remove_quiet(c)
        job._tmpfiles.clear()
