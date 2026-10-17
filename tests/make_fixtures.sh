#!/usr/bin/env bash
# Génère des médias de test libres (mire + tonalité) : MP4, HLS multi-qualité, DASH, HLS chiffré AES-128.
set -euo pipefail
OUT="$(cd "$(dirname "$0")" && pwd)/media"
rm -rf "$OUT" && mkdir -p "$OUT/hls" "$OUT/dash" "$OUT/hls_aes"
FF="ffmpeg -hide_banner -loglevel error -y"
SRC="-f lavfi -i testsrc2=size=1280x720:rate=25:duration=${DUR:-20} -f lavfi -i sine=frequency=440:duration=${DUR:-20}"
$FF $SRC -c:v libx264 -preset veryfast -g 50 -keyint_min 50 -sc_threshold 0 -b:v 1500k -c:a aac -b:a 128k -movflags +faststart "$OUT/sample.mp4"
$FF $SRC -c:v libvpx-vp9 -b:v 800k -deadline realtime -cpu-used 8 -c:a libopus "$OUT/sample.webm"
# HLS maître avec deux variantes (720p et 360p)
$FF $SRC -filter_complex "[0:v]split=2[a][b];[b]scale=640:360[b2]" \
  -map "[a]" -map "[b2]" -map 1:a -map 1:a \
  -c:v libx264 -preset veryfast -g 50 -keyint_min 50 -sc_threshold 0 -c:a aac -b:v:0 1500k -b:v:1 500k \
  -f hls -hls_time 2 -hls_playlist_type vod -master_pl_name master.m3u8 \
  -var_stream_map "v:0,a:0,name:720p v:1,a:1,name:360p" \
  -hls_segment_filename "$OUT/hls/%v_%03d.ts" "$OUT/hls/%v.m3u8"
# DASH
$FF $SRC -c:v libx264 -preset veryfast -g 50 -keyint_min 50 -sc_threshold 0 -b:v 1000k -c:a aac -f dash -seg_duration 2 "$OUT/dash/manifest.mpd"
# HLS chiffré AES-128 (chiffrement de transport standard, clé servie en clair : pas un DRM)
# Chemins relatifs dans keyinfo : sous Git Bash (Windows), un chemin /d/... écrit
# DANS un fichier n'est pas converti et ffmpeg.exe ne pourrait pas l'ouvrir.
head -c 16 /dev/urandom > "$OUT/hls_aes/enc.key"
printf 'enc.key\nenc.key\n' > "$OUT/hls_aes/keyinfo"
( cd "$OUT/hls_aes" && $FF $SRC -c:v libx264 -preset veryfast -g 50 -keyint_min 50 -sc_threshold 0 -c:a aac -f hls -hls_time 2 \
  -hls_playlist_type vod -hls_key_info_file keyinfo index.m3u8 )
rm "$OUT/hls_aes/keyinfo"
echo "OK -> $OUT"
