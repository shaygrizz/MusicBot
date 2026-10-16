"""Перенос данных с другого хостинга: база (musicbot.db) и картинки профилей (avatars.zip).
Только проверка и раскладка файлов; команду /restore в боте видит лишь администратор."""

import os
import re
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

from . import db, paths

NEEDED_TABLES = {"users", "plays", "favorites", "cache"}
AVATAR_FILE_MAX = 5 * 1024 * 1024


def check_database(path: Path) -> dict:
    """Это целая база бота? Возвращает число записей по таблицам, иначе RuntimeError."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise RuntimeError(f"не открывается как SQLite: {e}")
    try:
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("база повреждена (integrity_check)")
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        missing = NEEDED_TABLES - tables
        if missing:
            raise RuntimeError(f"это не база бота: нет таблиц {', '.join(sorted(missing))}")
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in sorted(NEEDED_TABLES)}
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"не база SQLite: {e}")
    finally:
        con.close()


async def replace_database(new: Path) -> tuple[dict, Path | None]:
    """Ставит присланную базу вместо текущей; текущую сохраняет рядом с отметкой времени.
    Бот открывает базу на каждый запрос, поэтому перезапуск не нужен."""
    counts = check_database(new)
    current = Path(db.DB_PATH)
    backup = None
    if current.exists():
        backup = current.with_name(f"{current.name}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copyfile(current, backup)
    tmp = current.with_name(current.name + ".restoring")
    shutil.copyfile(new, tmp)
    os.replace(tmp, current)
    await db.init_db()  # дописывает колонки, добавленные после старой версии (например, ссылка в избранном)
    return counts, backup


AVATAR_RE = re.compile(r"^[A-Za-z0-9_-]{16,40}\.jpg$")  # имена картинок — случайные токены, как TOKEN_RE в веб-части


def _avatar_name(name: str) -> str | None:
    """Архив пришёл извне, пути внутри ему не доверяем: берём только имя файла (avatars/x.jpg -> x.jpg)
    и принимаем его, лишь если это «<токен>.jpg». Так файл не может лечь за пределы папки с картинками."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base if AVATAR_RE.match(base) else None


def restore_avatars(archive: Path, dest: Path = paths.AVATARS) -> int:
    """Раскладывает картинки профилей из архива. Возвращает, сколько файлов положено."""
    dest.mkdir(parents=True, exist_ok=True)
    put = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > AVATAR_FILE_MAX:
                continue
            name = _avatar_name(info.filename)
            if not name:
                continue
            data = z.read(info)
            if not data.startswith(b"\xff\xd8"):  # не JPEG
                continue
            (dest / name).write_bytes(data)
            put += 1
    return put
