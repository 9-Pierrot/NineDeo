<p align="center"><img src="logo.png" width="120" alt="Logo NineDeo"></p>

<h1 align="center">NineDeo</h1>
<p align="center"><strong>Téléchargeur de vidéos pour navigateur : extension + moteur local (yt-dlp, ffmpeg)</strong></p>

<p align="center">
  <a href="https://github.com/9-Pierrot/NineDeo/actions/workflows/ci.yml"><img src="https://github.com/9-Pierrot/NineDeo/actions/workflows/ci.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/9-Pierrot/NineDeo/releases/latest"><img src="https://img.shields.io/github/v/release/9-Pierrot/NineDeo?label=t%C3%A9l%C3%A9charger&color=0029ff" alt="Dernière version"></a>
</p>

NineDeo détecte les vidéos des pages que vous visitez (fichiers MP4/WebM, flux adaptatifs HLS et DASH, lecteurs intégrés en iframe) et les télécharge dans la qualité choisie. Une extension de navigateur se charge de la détection et de l'interface. Le téléchargement est confié à un petit programme local, l'**hôte natif**, qui pilote [yt-dlp](https://github.com/yt-dlp/yt-dlp) et [ffmpeg](https://ffmpeg.org).

| Popup — flux HLS multi-qualité | Téléchargements en cours / terminés | Bouton sur la vidéo |
|---|---|---|
| ![](docs/images/popup-hls.png) | ![](docs/images/popup-telechargements.png) | ![](docs/images/bouton-video.png) |

## Fonctionnalités

- **Détection automatique** sur n'importe quel site : trafic réseau (MP4, WebM, MKV, MP3…, manifestes `.m3u8` et `.mpd`), balises `<video>`/`<audio>`, métadonnées Open Graph et lecteurs dans une iframe. Le nombre de vidéos trouvées s'affiche sur l'icône.
- **Analyse des manifestes** HLS/DASH : liste des qualités disponibles et regroupement des variantes sous le manifeste maître.
- **Analyse de page par yt-dlp** : plus de 1 800 sites reconnus, plus un extracteur générique. Elle donne le titre, la miniature, la durée et tous les formats exacts.
- **Repli automatique** : la page est essayée d'abord, puis les lecteurs en iframe, puis les flux détectés.
- **Qualités** : meilleure, 4K, 1440p, 1080p, 720p, 480p, 360p, audio seul en MP3/M4A, ou format exact. Les pistes audio et vidéo séparées sont fusionnées automatiquement.
- **File de téléchargements** : progression, débit, temps restant, pause/reprise (fichiers `.part`), annulation avec nettoyage, nouvel essai, ouverture du fichier ou du dossier.
- **Sites avec connexion** : les cookies du site sont transmis au moteur local (jamais enregistrés), de même que le Referer, l'User-Agent et une signature TLS de navigateur.
- **Bouton « Télécharger »** au survol des vidéos, **menu contextuel** (clic droit sur une vidéo, un lien ou la page), **raccourcis** `Alt+Maj+N` et `Alt+Maj+D`, **notifications**.
- **Réglages** : dossier, modèle de nom, conteneur MP4/MKV, priorité à H.264/AAC, métadonnées, miniature, sous-titres, téléchargements simultanés, limite de débit, playlists.
- **DRM** : les contenus protégés (Widevine, PlayReady, FairPlay) sont signalés et ne sont pas téléchargés.
- Fonctionne avec **Chrome, Edge, Brave, Opera, Vivaldi et Firefox 128+**, sous **Windows, Linux et macOS**.

## Installation en 3 minutes (Windows, sans Python)

1. Téléchargez **`NineDeo-x.y.z-windows.zip`** depuis la page [**Releases**](https://github.com/9-Pierrot/NineDeo/releases/latest) et décompressez-le.
2. Double-cliquez sur `installer\windows\install.bat` : le moteur pré-compilé est inclus, ffmpeg est téléchargé automatiquement.
3. Ouvrez `chrome://extensions` (ou `edge://extensions`), activez le **Mode développeur**, cliquez sur **Charger l'extension non empaquetée** et choisissez le dossier `extension`. Dans les paramètres NineDeo, cliquez sur **Tester la connexion**.

## Démarrage rapide depuis les sources (Windows)

```powershell
git clone https://github.com/9-Pierrot/NineDeo.git
```

1. **Moteur** : double-cliquez sur `installer\windows\install.bat`. Il faut Python 3.9+ ; ffmpeg est téléchargé automatiquement.
2. **Extension** : ouvrez `chrome://extensions` (ou `edge://extensions`), activez le *Mode développeur*, cliquez sur *Charger l'extension non empaquetée* et sélectionnez le dossier `extension`.
3. Dans les paramètres NineDeo qui s'ouvrent, cliquez sur **Tester la connexion** : « Moteur connecté » doit s'afficher.

Le mode d'emploi complet, avec Firefox et le dépannage, se trouve dans la [**notice d'utilisation**](docs/NOTICE_UTILISATION.md).

## Arborescence

```
NineDeo/
├── extension/              Extension Manifest V3 (Chrome, Edge, Firefox : même dossier)
│   ├── manifest.json
│   ├── background.js       service worker : détection réseau, manifestes, hôte natif, tâches
│   ├── content/            page-hook.js (EME, monde MAIN), drm-bridge.js, detector.js, overlay.js
│   ├── popup/              interface principale
│   ├── options/            page de paramètres
│   ├── shared/             util.js (classification, formats), dom.js, theme.css
│   └── icons/              générées depuis logo.png
├── host/                   Hôte natif Python
│   ├── ninedeo_host.py     point d'entrée lancé par le navigateur
│   └── ninedeo/            protocol.py, host.py, jobs.py, engine.py, config.py, system.py
├── installer/
│   ├── windows/            install.bat / install.ps1, uninstall, build_exe.ps1 (PyInstaller)
│   └── unix/install.sh     Linux et macOS
├── tests/                  pytest (hôte), e2e Chromium (Playwright), serveur et pages de test
├── tools/                  generate_icons.py, build_extension.py, keys/
└── docs/                   documentation technique, notice d'utilisation, schémas, captures
```

## Tests

```bash
bash tests/make_fixtures.sh                      # médias de test synthétiques (mire + son)
python -m pytest tests                           # 24 tests : unitaires + hôte réel via Native Messaging
python tests/e2e_extension.py --vendor <node_modules contenant hls.js>   # Chromium + extension + hôte
```

## Documentation

- [Documentation technique](docs/DOCUMENTATION_TECHNIQUE.md) : architecture, protocole, algorithmes, sécurité, tests, limites.
- [Notice d'utilisation](docs/NOTICE_UTILISATION.md) : installation, utilisation, réglages, dépannage.

## Cadre d'utilisation

NineDeo est un projet pédagogique. Il ne contourne aucune mesure technique de protection : les flux chiffrés par DRM sont détectés puis refusés. Téléchargez uniquement des contenus que vous avez le droit de copier (contenus libres, vos propres vidéos, copie privée autorisée), dans le respect des conditions d'utilisation des sites.
