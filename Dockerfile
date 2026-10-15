# Node.js нужен yt-dlp как JS-движок для разбора YouTube; берём готовый бинарник, а не старый пакет из apt
FROM node:22-slim AS node

FROM python:3.12-slim

# Python 3.12: на нём есть сборка ShazamIO, поэтому отдельное окружение, как дома, не нужно
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    WEBAPP_HOST=0.0.0.0 \
    WEBAPP_PORT=8080 \
    YTDLP_JS_RUNTIME=node \
    SHAZAM_PYTHON=/usr/local/bin/python

COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt requirements-docker.txt ./
RUN pip install -r requirements-docker.txt

# ffmpeg и ffprobe приходят пакетом static-ffmpeg; скачиваем их сейчас, чтобы бот не качал при каждом старте
RUN python -c "import static_ffmpeg; static_ffmpeg.add_paths()"

COPY . .

# Постоянное хранилище Amvera монтируется в /data (база, фото профилей, кэш избранного, cookies.txt)
CMD ["python", "bot.py"]
