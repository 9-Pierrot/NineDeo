#!/usr/bin/env python3
"""Empaquette l'extension pour la distribution.

Produit dans dist/ :
  * ninedeo-chromium-<version>.zip : Chrome, Edge, Brave, Opera, Vivaldi
  * ninedeo-firefox-<version>.zip  : Firefox 128+ (à signer sur addons.mozilla.org
                                     pour une installation permanente)

Le dossier extension/ reste chargeable tel quel dans les deux familles de
navigateurs (manifeste commun) ; ces archives retirent simplement les clés
propres à l'autre famille pour éviter les avertissements.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "extension"
DIST = ROOT / "dist"
SKIP = {".DS_Store", "Thumbs.db"}


def variant(manifest: dict, target: str) -> dict:
    m = json.loads(json.dumps(manifest))
    if target == "chromium":
        m.pop("browser_specific_settings", None)
        m["background"] = {"service_worker": m["background"]["service_worker"]}
    else:
        m.pop("key", None)
        m["background"] = {"scripts": m["background"]["scripts"]}
        # Firefox : « _execute_action » et options_ui fonctionnent à l'identique.
    return m


def build(target: str, manifest: dict) -> Path:
    DIST.mkdir(exist_ok=True)
    out = DIST / f"ninedeo-{target}-{manifest['version']}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(SRC.rglob("*")):
            if path.is_dir() or path.name in SKIP:
                continue
            rel = path.relative_to(SRC).as_posix()
            if rel == "manifest.json":
                z.writestr(rel, json.dumps(variant(manifest, target), ensure_ascii=False, indent=2))
            else:
                z.write(path, rel)
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest = json.loads((SRC / "manifest.json").read_text("utf-8"))
    for target in ("chromium", "firefox"):
        print("->", build(target, manifest).relative_to(ROOT))


if __name__ == "__main__":
    main()
