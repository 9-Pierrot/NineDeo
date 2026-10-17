"""Interface avec yt-dlp : options, préréglages de qualité, analyse, erreurs."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

log = logging.getLogger("ninedeo.engine")

HEIGHTS = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)

AUDIO_PRESETS = {"audio", "audio_mp3", "audio_m4a", "audio_best"}


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # pragma: no cover
        return "inconnue"


def impersonation_available() -> bool:
    try:
        import curl_cffi  # noqa: F401

        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Logger yt-dlp -> logging (yt-dlp ne doit JAMAIS écrire sur stdout)
# --------------------------------------------------------------------------
class YdlLogger:
    def __init__(self, prefix: str = "", on_warning=None) -> None:
        self.prefix = prefix
        self.on_warning = on_warning

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            log.debug("%s%s", self.prefix, msg)
        else:
            log.info("%s%s", self.prefix, msg)

    def info(self, msg: str) -> None:
        log.info("%s%s", self.prefix, msg)

    def warning(self, msg: str) -> None:
        log.warning("%s%s", self.prefix, msg)
        if self.on_warning:
            self.on_warning(msg)

    def error(self, msg: str) -> None:
        log.error("%s%s", self.prefix, msg)


# --------------------------------------------------------------------------
# Cookies transmis par l'extension (API chrome.cookies) -> fichier Netscape
# --------------------------------------------------------------------------
def write_cookie_file(cookies: Optional[Iterable[dict]]) -> Optional[str]:
    cookies = [c for c in (cookies or []) if isinstance(c, dict)]
    if not cookies:
        return None
    lines = ["# Netscape HTTP Cookie File", "# Généré par NineDeo — fichier temporaire", ""]
    seen = set()
    for c in cookies:
        name = str(c.get("name", ""))
        value = str(c.get("value", ""))
        domain = str(c.get("domain", "")).strip()
        if not name or not domain or any(ch in name + value + domain for ch in "\t\r\n"):
            continue
        host_only = bool(c.get("hostOnly"))
        if host_only:
            domain = domain.lstrip(".")
        elif not domain.startswith("."):
            domain = "." + domain
        path = str(c.get("path") or "/")
        key = (domain, path, name)
        if key in seen:
            continue
        seen.add(key)
        expires = 0 if c.get("session") else int(float(c.get("expirationDate") or 0))
        lines.append("\t".join([
            domain,
            "FALSE" if host_only else "TRUE",
            path,
            "TRUE" if c.get("secure") else "FALSE",
            str(expires),
            name,
            value,
        ]))
    if len(lines) <= 3:
        return None
    fd, path = tempfile.mkstemp(prefix="ninedeo-cookies-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def remove_quiet(path: Optional[str]) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Préréglages -> sélecteur de format yt-dlp
# --------------------------------------------------------------------------
def format_selector(preset: str, format_id: Optional[str], has_ffmpeg: bool) -> str:
    """Construit l'expression ``-f`` de yt-dlp.

    Chaque expression se termine par un repli générique (``/b``) pour les
    sources qui n'exposent pas de hauteur (fichier MP4 direct, flux sans
    métadonnées) : on préfère télécharger quelque chose plutôt qu'échouer.
    """
    if format_id:
        # Format exact choisi dans la liste avancée : si c'est une piste
        # vidéo seule, on lui ajoute la meilleure piste audio.
        return f"{format_id}+ba/{format_id}" if has_ffmpeg else format_id

    if preset in AUDIO_PRESETS:
        if preset == "audio_m4a":
            return "ba[ext=m4a]/ba/b"
        return "ba/b"

    if preset == "worst":
        return "wv*+wa/w" if has_ffmpeg else "w"

    m = re.fullmatch(r"h(\d{3,4})", preset or "")
    if m:
        h = int(m.group(1))
        if has_ffmpeg:
            return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
        return f"b[height<={h}]/b"

    # "best" et valeur inconnue
    return "bv*+ba/b" if has_ffmpeg else "b"


def audio_target(preset: str, cfg: Dict[str, Any]) -> Optional[str]:
    if preset not in AUDIO_PRESETS:
        return None
    if preset == "audio_m4a":
        return "m4a"
    if preset == "audio_mp3":
        return "mp3"
    if preset == "audio_best":
        return "best"
    return cfg.get("audio_format") or "mp3"


def sanitize_title(title: str) -> str:
    from yt_dlp.utils import sanitize_filename

    return sanitize_filename((title or "").strip(), restricted=False)[:180].strip(" .") or "video"


def unique_base(outdir: str, title: str) -> str:
    """Nom de base libre dans ``outdir`` : « Titre », « Titre (2) », …"""
    import glob as _glob

    base = sanitize_title(title)
    n = 1
    while True:
        cand = base if n == 1 else f"{base} ({n})"
        taken = [
            f for f in _glob.glob(_glob.escape(os.path.join(outdir, cand)) + ".*")
            if not f.endswith((".part", ".ytdl")) and "-Frag" not in f and ".part-" not in f
        ]
        if not taken:
            return cand
        n += 1


def literal_template(title: str) -> str:
    """Modèle de nom de fichier à partir d'un titre fixé (flux direct)."""
    return sanitize_title(title).replace("%", "%%") + ".%(ext)s"


def build_options(
    cfg: Dict[str, Any],
    *,
    preset: str,
    format_id: Optional[str],
    headers: Optional[Dict[str, str]],
    cookie_file: Optional[str],
    ffmpeg: Optional[str],
    title_override: Optional[str] = None,
    playlist: Optional[bool] = None,
) -> Dict[str, Any]:
    has_ffmpeg = bool(ffmpeg)
    outdir = cfg["download_dir"]
    os.makedirs(outdir, exist_ok=True)
    template = literal_template(title_override) if title_override else cfg["filename_template"]
    allow_playlist = cfg["allow_playlists"] if playlist is None else playlist
    if allow_playlist and not title_override and "%(playlist" not in template:
        # Numérotation des éléments d'une playlist ; vide pour une vidéo seule.
        template = "%(playlist_index&{:03d} - |)s" + template

    opts: Dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "no_color": True,
        "paths": {"home": outdir},
        "outtmpl": {"default": template},
        "windowsfilenames": True,
        "trim_file_name": 200,
        "noplaylist": not allow_playlist,
        "continuedl": True,
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": int(cfg.get("concurrent_fragments") or 4),
        "updatetime": False,
        "format": format_selector(preset, format_id, has_ffmpeg),
        "http_headers": {k: v for k, v in (headers or {}).items() if v},
        "postprocessors": [],
        "check_formats": None,
    }
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
        opts["merge_output_format"] = cfg.get("container") or "mp4"
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if cfg.get("rate_limit_kib"):
        opts["ratelimit"] = int(cfg["rate_limit_kib"]) * 1024
    if cfg.get("prefer_compatible") and preset not in AUDIO_PRESETS:
        # À résolution égale : H.264 + AAC (lisibles partout sous Windows).
        opts["format_sort"] = ["res", "fps", "vcodec:h264", "acodec:aac"]
    if cfg.get("impersonate") and impersonation_available():
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            opts["impersonate"] = ImpersonateTarget("chrome")
        except Exception:  # pragma: no cover
            pass

    pps: List[Dict[str, Any]] = opts["postprocessors"]
    target = audio_target(preset, cfg)
    if target and has_ffmpeg:
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": target}
        if target == "mp3":
            pp["preferredquality"] = str(cfg.get("audio_quality") or "192")
        pps.append(pp)

    if has_ffmpeg and cfg.get("embed_subtitles") and not target:
        langs = [l.strip() for l in str(cfg.get("subtitle_langs") or "").split(",") if l.strip()]
        opts.update({"writesubtitles": True, "subtitleslangs": langs or ["fr", "en"]})
        pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

    if has_ffmpeg and cfg.get("embed_metadata"):
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})

    if has_ffmpeg and cfg.get("embed_thumbnail"):
        opts["writethumbnail"] = True
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    return opts


# --------------------------------------------------------------------------
# Erreurs -> code + message en français
# --------------------------------------------------------------------------
_ERROR_RULES: List[Tuple[str, str, str]] = [
    (r"drm", "drm",
     "Ce contenu est protégé par DRM : NineDeo ne prend pas en charge son téléchargement."),
    (r"unsupported url", "unsupported",
     "Aucune vidéo exploitable trouvée à cette adresse. Lancez la lecture de la vidéo "
     "puis choisissez un flux dans « Flux détectés »."),
    (r"no video formats found|no media found", "unsupported",
     "Aucune vidéo trouvée sur cette page. Lancez la lecture puis utilisez « Flux détectés »."),
    (r"requested format is not available", "format_unavailable",
     "La qualité demandée n'est pas disponible pour cette vidéo."),
    (r"http error 403|forbidden", "forbidden",
     "Accès refusé par le serveur (403). Le lien a peut-être expiré : rechargez la page et réessayez."),
    (r"http error 404|not found", "not_found",
     "Fichier introuvable (404). Rechargez la page et réessayez."),
    (r"http error 429|too many requests", "rate_limited",
     "Trop de requêtes : le site limite les téléchargements. Réessayez plus tard."),
    (r"sign in|log in|login|cookies|private video|members[- ]only|confirm you.re not a bot", "auth_required",
     "Le site demande une connexion. Connectez-vous dans le navigateur puis réessayez "
     "(option « Utiliser les cookies du navigateur » activée)."),
    (r"ffmpeg.*(not found|not installed)|ffprobe.*not found", "ffmpeg_missing",
     "ffmpeg est introuvable. Relancez l'installeur de NineDeo."),
    (r"no space left|errno 28", "disk_full", "Espace disque insuffisant."),
    (r"permission denied|errno 13", "permission", "Écriture impossible dans le dossier de destination."),
    (r"live event will begin|premieres in|this live event", "not_started",
     "Le direct n'a pas encore commencé."),
    (r"geo.?restrict|not available in your country", "geo_blocked",
     "Vidéo indisponible dans votre pays."),
    (r"timed out|timeout|connection reset|getaddrinfo|temporary failure in name resolution", "network",
     "Problème réseau : vérifiez votre connexion et réessayez."),
]


def classify_error(exc: BaseException) -> Tuple[str, str, str]:
    raw = str(exc)
    raw = re.sub(r"^ERROR:\s*", "", raw).strip()
    low = raw.lower()
    for pattern, code, message in _ERROR_RULES:
        if re.search(pattern, low):
            return code, message, raw
    return "error", "Le téléchargement a échoué.", raw


# --------------------------------------------------------------------------
# Analyse (probe) d'une URL sans télécharger
# --------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _compact_format(f: Dict[str, Any]) -> Dict[str, Any]:
    vcodec = f.get("vcodec") or "none"
    acodec = f.get("acodec") or "none"
    return {
        "id": f.get("format_id"),
        "ext": f.get("ext"),
        "height": f.get("height"),
        "width": f.get("width"),
        "fps": f.get("fps"),
        "vcodec": None if vcodec == "none" else vcodec.split(".")[0],
        "acodec": None if acodec == "none" else acodec.split(".")[0],
        "tbr": _num(f.get("tbr")),
        "size": f.get("filesize") or f.get("filesize_approx"),
        "protocol": f.get("protocol"),
        "note": f.get("format_note"),
        "drm": bool(f.get("has_drm")),
    }


def summarize_info(info: Dict[str, Any]) -> Dict[str, Any]:
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        return {
            "kind": "playlist",
            "title": info.get("title") or "Playlist",
            "extractor": info.get("extractor_key") or info.get("extractor"),
            "webpage_url": info.get("webpage_url"),
            "count": info.get("playlist_count") or len(entries),
            "entries": [
                {"title": e.get("title"), "url": e.get("url") or e.get("webpage_url"), "duration": e.get("duration")}
                for e in entries[:50]
            ],
            "thumbnail": info.get("thumbnail") or (entries[0].get("thumbnail") if entries else None),
        }

    raw_formats = info.get("formats") or [info]
    formats = []
    for f in raw_formats:
        if (f.get("vcodec") == "none" and f.get("acodec") == "none") or f.get("ext") == "mhtml":
            continue  # storyboards, images
        formats.append(_compact_format(f))
    formats.sort(key=lambda f: ((f["height"] or 0), (f["tbr"] or 0)), reverse=True)
    heights = sorted({f["height"] for f in formats if f["height"] and f["vcodec"]}, reverse=True)
    has_audio_only = any(f["acodec"] and not f["vcodec"] for f in formats)
    drm = bool(formats) and all(f["drm"] for f in formats)
    return {
        "kind": "video",
        "id": info.get("id"),
        "title": info.get("title") or info.get("id") or "Vidéo",
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "webpage_url": info.get("webpage_url") or info.get("original_url"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "is_live": bool(info.get("is_live")),
        "heights": heights,
        "has_audio_only": has_audio_only,
        "drm": drm,
        "formats": formats[:120],
    }


def probe(url: str, cfg: Dict[str, Any], headers: Optional[Dict[str, str]], cookie_file: Optional[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_color": True,
        "skip_download": True,
        "noplaylist": not cfg.get("allow_playlists"),
        "extract_flat": "in_playlist",
        "socket_timeout": 20,
        "http_headers": {k: v for k, v in (headers or {}).items() if v},
        "logger": YdlLogger("[probe] "),
        "check_formats": None,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if cfg.get("impersonate") and impersonation_available():
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            opts["impersonate"] = ImpersonateTarget("chrome")
        except Exception:  # pragma: no cover
            pass
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        info = ydl.sanitize_info(info)
    return summarize_info(info)


__all__ = [
    "DownloadError", "ExtractorError", "YdlLogger", "build_options", "classify_error",
    "format_selector", "impersonation_available", "probe", "remove_quiet",
    "summarize_info", "write_cookie_file", "ytdlp_version",
]
