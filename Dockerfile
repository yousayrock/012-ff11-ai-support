FROM python:3.11-slim

WORKDIR /app

# PortAudio（sounddevice依存）+ FFmpeg（faster-whisper依存）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libportaudio2 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY 012_requirements.txt .

# pywebview はコンテナ不要（デスクトップ専用）
RUN pip install --no-cache-dir -r 012_requirements.txt \
    && pip uninstall -y pywebview || true

COPY 012_server.py .
COPY 012_ff11.html .
COPY mia/ ./mia/

ENV HTTP_HOST=0.0.0.0

EXPOSE 8012 9012

CMD ["python", "012_server.py"]
