FROM python:3.12-slim

# ffmpeg, curl, netcat, dos2unix
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        netcat-openbsd \
        dos2unix \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# bgutil PO token server
RUN curl -fsSL \
    "https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64" \
    -o /usr/local/bin/bgutil-pot \
    && chmod +x /usr/local/bin/bgutil-pot

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py extensions.py models.py ./
COPY blueprints/ blueprints/
COPY services/ services/
COPY templates/ templates/
COPY static/ static/
COPY start.sh .
RUN dos2unix start.sh && chmod +x start.sh

EXPOSE 5000

ENV FLASK_ENV=production
ENV BGU_POT_SERVER_HOST=localhost:4416

CMD ["./start.sh"]
