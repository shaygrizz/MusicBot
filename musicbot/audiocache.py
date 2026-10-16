"""Общий дисковый кэш звука: песня, скачанная один раз (любым пользователем), дальше берётся с диска за миллисекунды.
Ключ — ссылка на источник или «ленивая» ссылка на трек из каталога. Старые файлы вытесняются по размеру папки."""

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import paths
from .download import Downloaded

CACHE_DIR = paths.AUDIO
# на хостинге кэш делит постоянный диск с базой: заполненный диск сломал бы запись в базу, поэтому запас скромный
MAX_BYTES = int(os.getenv("AUDIO_CACHE_MB", "1000")) * 1024 * 1024
AUDIO_EXT = (".m4a", ".mp3")
_lock = threading.Lock()


def _name(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()


def _audio(name: str) -> Path | None:
    for ext in AUDIO_EXT:
        p = CACHE_DIR / f"{name}{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def path_for(key: str) -> Path | None:
    """Файл в кэше без копирования: для отдачи с диска (плеер в мини-аппе)."""
    return _audio(_name(key))


def get(key: str, workdir_root: Path) -> Downloaded | None:
    """Копия файла из кэша в новую рабочую папку (вызывающий её потом удаляет, кэш остаётся целым)."""
    name = _name(key)
    src = _audio(name)
    meta_path = CACHE_DIR / f"{name}.json"
    if src is None or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        workdir = workdir_root / uuid.uuid4().hex[:10]
        workdir.mkdir(parents=True)
        dst = workdir / f"{meta.get('id') or name}{src.suffix}"
        shutil.copyfile(src, dst)
        thumb = None
        cover = CACHE_DIR / f"{name}.jpg"
        if cover.exists():
            thumb = workdir / "cover.jpg"
            shutil.copyfile(cover, thumb)
        now = time.time()
        os.utime(src, (now, now))  # недавно использованное вытесняется последним
    except Exception as e:
        logging.info("кэш звука: не удалось взять %s: %s", key[:80], e)
        return None
    return Downloaded(
        path=dst,
        thumb=thumb,
        title=meta.get("title", ""),
        artist=meta.get("artist", ""),
        duration=int(meta.get("duration") or 0),
        source="cache",
        workdir=workdir,
    )


def _place(src: Path, dest: Path) -> None:
    """Жёсткая ссылка на уже лежащий в кэше файл (место на диске не тратится), иначе копия."""
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:6]}.part")
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def put(keys: list[str], d: Downloaded) -> None:
    """Сохраняет скачанный файл под всеми ключами. На диске он один: остальные ключи — жёсткие ссылки на него."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        meta = {"title": d.title, "artist": d.artist, "duration": d.duration, "source": d.source, "id": d.path.stem}
        first_audio = first_cover = None
        for key in dict.fromkeys(k for k in keys if k):
            name = _name(key)
            have = _audio(name)
            if have:
                first_audio = first_audio or have
                continue
            dest = CACHE_DIR / f"{name}{d.path.suffix.lower()}"
            if first_audio:
                _place(first_audio, dest)
            else:
                tmp = CACHE_DIR / f"{name}.{uuid.uuid4().hex[:6]}.part"
                shutil.copyfile(d.path, tmp)
                os.replace(tmp, dest)
                first_audio = dest
            if d.thumb and d.thumb.exists():
                cover = CACHE_DIR / f"{name}.jpg"
                if first_cover:
                    _place(first_cover, cover)
                else:
                    shutil.copyfile(d.thumb, cover)
                    first_cover = cover
            (CACHE_DIR / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _prune()
    except Exception as e:
        logging.warning("кэш звука: не удалось сохранить: %s", e)


def _prune() -> None:
    """Размер считаем по настоящим файлам: несколько жёстких ссылок на один файл занимают место один раз."""
    with _lock:
        files = [(p, p.stat()) for p in CACHE_DIR.iterdir() if p.suffix in AUDIO_EXT]
        sizes = {(st.st_dev, st.st_ino): st.st_size for _, st in files}
        total = sum(sizes.values())
        if total <= MAX_BYTES:
            return
        links: dict[tuple, int] = {}
        for _, st in files:
            links[(st.st_dev, st.st_ino)] = links.get((st.st_dev, st.st_ino), 0) + 1
        for p, st in sorted(files, key=lambda f: f[1].st_mtime):
            ino = (st.st_dev, st.st_ino)
            for ext in (*AUDIO_EXT, ".json", ".jpg"):
                p.with_suffix(ext).unlink(missing_ok=True)
            links[ino] -= 1
            if links[ino] == 0:  # удалена последняя ссылка — место освободилось
                total -= sizes[ino]
            if total <= MAX_BYTES * 0.9:
                break
