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

CMD ["./start.sh"]
