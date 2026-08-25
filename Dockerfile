# syntax=docker/dockerfile:1
FROM python:3.11-slim

# ffmpeg is a hard runtime dependency: faster-whisper shells out to it to decode
# anything that is not already PCM WAV, which is every mp3, m4a and flac the
# validator accepts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs containers as UID 1000, and several platforms do the
# same. Running as root locally and non-root there would mean the SQLite
# database and the model cache are unwritable in production only — so create the
# user here and own the paths from the start.
RUN useradd --create-home --uid 1000 app
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

# Bake the weights into the image rather than fetching them on the first
# request. app.py warms the model singleton at startup, but that warm-up is
# useless if the weights are not on disk yet — the first upload pays the
# download. Costs a layer at build time and removes it from every cold start.
ARG WHISPER_MODEL=tiny
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER_MODEL')"

# SQLite lives here; mount a volume to keep call records across restarts.
RUN mkdir -p /app/data && chown -R app:app /app

USER app

EXPOSE 7860

CMD ["python", "app.py"]
