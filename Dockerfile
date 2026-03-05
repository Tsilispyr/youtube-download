FROM python:3.12-slim

# Install system deps: ffmpeg (required by yt-dlp for audio conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app.py .
COPY templates/ templates/

# Create downloads dir
RUN mkdir -p /downloads

EXPOSE 5000

ENV DOWNLOAD_DIR=/downloads
ENV FLASK_ENV=production

CMD ["python", "app.py"]
