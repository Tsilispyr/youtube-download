#!/bin/sh
# AudioWeb / AudioWorld — Setup & run script
# Creates .env from .env.example, then starts Docker Compose with persistent volumes.
# MinIO ports 19000/19001 avoid conflicts with existing services on 9000/9001.

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
info()  { printf "${GREEN}[setup]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[setup]${NC} %s\n" "$*"; }
error() { printf "${RED}[setup]${NC} %s\n" "$*"; exit 1; }

cd "$(dirname "$0")"

# ── Create .env if missing ─────────────────────────────────────────────────
if [ ! -f .env ]; then
    info "Creating .env from .env.example..."
    cp .env.example .env
    warn "Edit .env to set SECRET_KEY, MAIL_USERNAME, MAIL_PASSWORD, etc."
else
    info ".env exists"
fi

# ── Ensure docker & docker compose ─────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    error "Docker not found. Install: https://docs.docker.com/get-docker/"
fi
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    error "Docker Compose not found. Install: https://docs.docker.com/compose/install/"
fi

# ── Info ───────────────────────────────────────────────────────────────────
info ""
info "Ports (customize via .env: MINIO_API_PORT, MINIO_CONSOLE_PORT, APP_PORT)"
info "  App:        http://localhost:5000"
info "  MinIO UI:   http://localhost:9001"
info "  MinIO login: minioadmin / minioadmin"
info ""
info "Data persists in Docker volumes: postgres_data, minio_data"
info ""

# ── Build & run ────────────────────────────────────────────────────────────
info "Building and starting services..."
$COMPOSE up --build -d

info "Done. View logs: $COMPOSE logs -f yt-mp3"
