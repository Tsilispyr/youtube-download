FROM python:3.12-slim

# ── 1. System packages ────────────────────────────────────────────────────────
# ffmpeg        → audio conversion (yt-dlp post-processor)
# curl, git     → Node.js setup + bgutil server clone
# netcat        → port-readiness check in start.sh
# nodejs / npm  → bgutil token server runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        netcat-openbsd \
        # Chromium system libraries required by bgutil's headless browser (puppeteer)
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libexpat1 \
        libfontconfig1 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
        wget \
        xdg-utils \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── 2. bgutil token server ────────────────────────────────────────────────────
# Clone the server source, build it with TypeScript compiler.
# We pin to the latest stable tag so builds are reproducible.
# To upgrade: change the --branch value below.
ARG BGUTIL_VERSION=1.3.1
RUN git clone --depth 1 --single-branch --branch ${BGUTIL_VERSION} \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
        /bgutil-server-src \
    && cd /bgutil-server-src/server \
    && npm ci \
    && npx tsc \
    && echo "bgutil server built OK"

# ── 3. Python packages ────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 4. App files ──────────────────────────────────────────────────────────────
COPY app.py .
COPY templates/ templates/
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 5000

ENV FLASK_ENV=production
# bgutil plugin reads this to find the HTTP token server
ENV BGU_POT_SERVER_HOST=localhost:4416
# Required for headless Chromium inside Docker (no user namespace sandbox available)
ENV PUPPETEER_ARGS="--no-sandbox --disable-setuid-sandbox"

CMD ["./start.sh"]
