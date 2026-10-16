"""Переезд: присланная база и картинки профилей проверяются и раскладываются безопасно."""

import sqlite3
import zipfile

import pytest

from musicbot import db, restore

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 20
TOKEN = "KP2m2jA10j1F6L933pZt2g"


def old_database(path):
    """База прошлой версии: в избранном ещё нет колонки со ссылкой."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, name TEXT, first_seen INTEGER);
        CREATE TABLE plays (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, artist TEXT,
            duration INTEGER, source TEXT, url TEXT, kind TEXT, ts INTEGER);
        CREATE TABLE cache (key TEXT PRIMARY KEY, file_id TEXT, title TEXT, artist TEXT, duration INTEGER);
        CREATE TABLE favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, key TEXT NOT NULL,
            title TEXT, artist TEXT, duration INTEGER, cover TEXT, ts INTEGER, UNIQUE(user_id, key));
        INSERT INTO users VALUES (1, 'Rese', 0);
        INSERT INTO favorites(user_id, key, title, artist, duration, cover, ts)
            VALUES (1, 'aurora|runaway', 'Runaway', 'AURORA', 248, '', 1);
        """
    )
    con.commit()
    con.close()


async def test_database_from_the_old_host_replaces_the_new_empty_one(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "musicbot.db"))
    await db.init_db()  # новый бот уже успел создать пустую базу
    incoming = tmp_path / "incoming.db"
    old_database(incoming)
    counts, backup = await restore.replace_database(incoming)
    assert counts == {"cache": 0, "favorites": 1, "plays": 0, "users": 1}
    assert backup and backup.exists()  # прежняя база не потеряна
    favs = await db.fav_list(1)
    assert [(f["artist"], f["title"], f["url"]) for f in favs] == [("AURORA", "Runaway", "")]  # колонка дописана


def test_files_that_are_not_the_bot_database_are_refused(tmp_path):
    junk = tmp_path / "x.db"
    junk.write_bytes(b"not a database at all" * 10)
    with pytest.raises(RuntimeError):
        restore.check_database(junk)
    other = tmp_path / "other.db"
    sqlite3.connect(other).executescript("CREATE TABLE notes (t TEXT);")
    with pytest.raises(RuntimeError, match="не база бота"):
        restore.check_database(other)


def test_profile_pictures_are_unpacked_only_inside_their_folder(tmp_path):
    archive = tmp_path / "avatars.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"{TOKEN}.jpg", JPEG)
        z.writestr("avatars/9pL4UpKfHHDLlPm6ATcTyw.jpg", JPEG)  # во вложенной папке — берём по имени
        z.writestr("../../v0g7l--bm1J63chxwKBXUg.jpg", JPEG)  # попытка выйти из папки — ляжет внутрь
        z.writestr("../evil.jpg", JPEG)  # не похоже на имя картинки — пропускаем
        z.writestr("1MF9rw14yYDEfe3aezoIpg.jpg", b"not a jpeg")  # не JPEG — пропускаем
        z.writestr("readme.txt", b"hi")
    dest = tmp_path / "avatars"
    assert restore.restore_avatars(archive, dest) == 3
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        [f"{TOKEN}.jpg", "9pL4UpKfHHDLlPm6ATcTyw.jpg", "v0g7l--bm1J63chxwKBXUg.jpg"]
    )
    assert not (tmp_path / "evil.jpg").exists() and not (tmp_path.parent / "v0g7l--bm1J63chxwKBXUg.jpg").exists()
