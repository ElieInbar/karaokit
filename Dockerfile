FROM python:3.12-slim

# ffmpeg: decoding/encoding everywhere; mkvtoolnix: the final .mkv; git + build tools: the
# ctc-forced-aligner dependency is installed from its git repository.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg mkvtoolnix git build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch: the GPU work happens on Modal (or slowly on this CPU in local mode), and
# the default Linux wheels would pull in several GB of CUDA libraries for nothing.
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml ./
COPY src ./src
COPY deploy ./deploy
RUN pip install --no-cache-dir .

ENV KARAOKIT_DATA_DIR=/data \
    HF_HOME=/data/cache/huggingface \
    AUDIO_SEPARATOR_MODEL_DIR=/data/cache/audio-separator-models \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "karaoke_he.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
