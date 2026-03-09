#!/bin/sh
# start.sh — starts the bgutil PO token server then the Flask app.
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { printf "${GREEN}[start]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[start]${NC} %s\n" "$*"; }
error() { printf "${RED}[start]${NC} %s\n" "$*"; exit 1; }

# ── Locate bgutil-pot binary ──────────────────────────────────────────────────
# Docker: installed to /usr/local/bin/bgutil-pot by Dockerfile
# Local dev without Docker: install manually —
#   curl -fsSL https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64 \
#     -o /usr/local/bin/bgutil-pot && chmod +x /usr/local/bin/bgutil-pot
if ! command -v bgutil-pot >/dev/null 2>&1; then
    error "bgutil-pot not found. See Dockerfile comment for install instructions."
fi
info "bgutil-pot found at $(command -v bgutil-pot) ✓"

# ── Check Python packages ─────────────────────────────────────────────────────
if ! python -c "import flask, yt_dlp" >/dev/null 2>&1; then
    warn "Python packages missing — installing..."
    pip install --no-cache-dir -r requirements.txt
else
    info "Python packages ✓"
fi

# ── Check ffmpeg ──────────────────────────────────────────────────────────────
if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg not found — MP3 conversion will fail."
    warn "Install: https://ffmpeg.org/download.html"
else
    info "ffmpeg ✓"
fi

# ── Start bgutil PO token server ──────────────────────────────────────────────
info "Starting bgutil-pot PO token server on port 4416..."
bgutil-pot server &
POT_PID=$!

# ── Wait until port 4416 accepts connections (max 30 s) ──────────────────────
info "Waiting for token server on port 4416..."
i=0
until nc -z localhost 4416 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
        warn "Token server did not respond in 30 s — starting Flask anyway."
        break
    fi
    sleep 1
done
nc -z localhost 4416 2>/dev/null && info "Token server ready (PID $POT_PID) ✓"

# ── Start Flask (replaces this shell as PID 1 for clean signal handling) ──────
info "Starting Flask app..."
exec python app.py
