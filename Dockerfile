# syntax=docker/dockerfile:1
FROM python:3.11-slim

# ffmpeg is a hard runtime dependency: faster-whisper shells out to it to decode
# anything that is not already PCM WAV, which is every mp3, m4a and flac the
# validator accepts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source, so editing a .py file does not invalidate the pip
# layer. pyproject.toml is the single source of dependency truth — there is no
# requirements.txt to drift from it.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

COPY app.py ./

# 127.0.0.1 inside a container is the container's own loopback, so a published
# port would map to nothing. app.py reads this and binds accordingly.
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    PYTHONUNBUFFERED=1

# Whisper weights are downloaded on first use and cached here. Mount a volume at
# this path to avoid re-downloading on every container start.
ENV HF_HOME=/app/.cache/huggingface

# SQLite lives here; mount a volume to keep call records across restarts.
RUN mkdir -p /app/data

EXPOSE 7860

CMD ["python", "app.py"]
