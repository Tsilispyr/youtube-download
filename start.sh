#!/bin/sh
# start.sh — starts the bgutil PO token server then the Flask app.
# Works inside Docker and on bare Linux/macOS for local dev.
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { printf "${GREEN}[start]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[start]${NC} %s\n" "$*"; }
error() { printf "${RED}[start]${NC} %s\n" "$*"; exit 1; }

# ── Locate bgutil server build ────────────────────────────────────────────────
# Inside Docker: /bgutil-server-src/server/build/main.js  (built in Dockerfile)
# Local dev:     ~/bgutil-ytdlp-pot-provider/server/build/main.js (cloned manually)
BGUTIL_MAIN=""
for candidate in \
    "/bgutil-server-src/server/build/main.js" \
    "$HOME/bgutil-ytdlp-pot-provider/server/build/main.js" \
    "./bgutil-ytdlp-pot-provider/server/build/main.js"
do
    if [ -f "$candidate" ]; then
        BGUTIL_MAIN="$candidate"
        break
    fi
done

# ── Install bgutil locally if not found ──────────────────────────────────────
if [ -z "$BGUTIL_MAIN" ]; then
    warn "bgutil server not found — cloning and building now (one-time setup)..."

    # Determine latest tag or fall back to 1.3.1
    BGUTIL_VERSION="${BGUTIL_VERSION:-1.3.1}"

    if ! command -v node >/dev/null 2>&1; then
        error "Node.js is required but not installed. Install from https://nodejs.org"
    fi
    if ! command -v git >/dev/null 2>&1; then
        error "git is required but not installed."
    fi

    git clone --depth 1 --single-branch --branch "$BGUTIL_VERSION" \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
        "$HOME/bgutil-ytdlp-pot-provider"

    cd "$HOME/bgutil-ytdlp-pot-provider/server"
    npm ci
    npx tsc
    cd -

    BGUTIL_MAIN="$HOME/bgutil-ytdlp-pot-provider/server/build/main.js"
    info "bgutil server built at $BGUTIL_MAIN ✓"
else
    info "bgutil server found at $BGUTIL_MAIN ✓"
fi

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

# ── Start bgutil token server ─────────────────────────────────────────────────
info "Starting bgutil PO token server..."
node "$BGUTIL_MAIN" &
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
