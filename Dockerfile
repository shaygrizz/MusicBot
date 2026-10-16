FROM python:3.12.14-slim-bookworm

# Python 3.12: на нём есть сборка ShazamIO, поэтому отдельное окружение, как дома, не нужно
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    WEBAPP_HOST=0.0.0.0 \
    WEBAPP_PORT=8080 \
    YTDLP_JS_RUNTIME=node \
    SHAZAM_PYTHON=/usr/local/bin/python

WORKDIR /app

# Node.js нужен yt-dlp как JS-движок для разбора YouTube. Он приходит пакетом pip (nodejs-wheel-binaries),
# так не нужен второй базовый образ: его загрузка в сборщике Amvera обрывалась
COPY requirements.txt requirements-docker.txt ./
RUN pip install -r requirements-docker.txt \
    && NODE="$(python -c 'import os, nodejs_wheel; print(os.path.join(os.path.dirname(nodejs_wheel.__file__), "bin", "node"))')" \
    && chmod +x "$NODE" && ln -sf "$NODE" /usr/local/bin/node && node --version

# ffmpeg и ffprobe приходят пакетом static-ffmpeg; скачиваем их сейчас, чтобы бот не качал при каждом старте
RUN python -c "import static_ffmpeg; static_ffmpeg.add_paths()"

COPY . .

# Постоянное хранилище Amvera монтируется в /data (база, фото профилей, кэш избранного, cookies.txt)
CMD ["python", "-m", "musicbot"]
