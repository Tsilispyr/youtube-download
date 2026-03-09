FROM python:3.12-slim

# ── 1. System packages ────────────────────────────────────────────────────────
# ffmpeg        → audio conversion (yt-dlp post-processor)
# curl          → download bgutil-pot Rust binary
# netcat        → port-readiness check in start.sh
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        netcat-openbsd \
        dos2unix \
    && rm -rf /var/lib/apt/lists/*

# ── 2. bgutil PO token server (Rust binary — no Chromium required) ────────────
# bgutil-ytdlp-pot-provider-rs generates PO tokens natively in Rust.
# No headless browser, no sandbox issues, no system library dependencies.
# Same HTTP API on port 4416 as the TypeScript version.
RUN curl -fsSL \
    "https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64" \
    -o /usr/local/bin/bgutil-pot \
    && chmod +x /usr/local/bin/bgutil-pot \
    && echo "bgutil-pot binary installed OK"

# ── 3. Python packages ────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 4. App files ──────────────────────────────────────────────────────────────
COPY app.py .
COPY templates/ templates/
COPY start.sh .
# Fix Windows CRLF line endings automatically — safe no-op on Linux/Mac
RUN dos2unix start.sh && chmod +x start.sh

EXPOSE 5000

ENV FLASK_ENV=production
# bgutil plugin reads this to find the HTTP token server
ENV BGU_POT_SERVER_HOST=localhost:4416

CMD ["./start.sh"]
