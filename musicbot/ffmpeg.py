"""ffmpeg и ffprobe: пакет static-ffmpeg скачивает их при первом запуске и добавляет в PATH."""

import static_ffmpeg


def ensure() -> None:
    static_ffmpeg.add_paths()
