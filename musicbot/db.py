import re
import time

import aiosqlite

from . import paths

DB_PATH = str(paths.DB)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    first_seen INTEGER
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    artist TEXT,
    duration INTEGER,
    source TEXT,
    url TEXT,
    kind TEXT,
    ts INTEGER
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    file_id TEXT,
    title TEXT,
    artist TEXT,
    duration INTEGER
);
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    duration INTEGER,
    cover TEXT,
    ts INTEGER,
    UNIQUE(user_id, key)
);
CREATE TABLE IF NOT EXISTS search_cache (
    key TEXT PRIMARY KEY,
    ts INTEGER,
    data TEXT
);
"""

# колонки, добавленные позже: у уже существующей базы их нет, поэтому дописываем при запуске
MIGRATIONS = [
    ("users", "display_name", "TEXT"),
    ("users", "avatar_token", "TEXT"),
    ("users", "bg_token", "TEXT"),
    ("plays", "cover", "TEXT"),
    ("favorites", "url", "TEXT"),  # ссылка на источник (песня из плейлиста YouTube): качаем по ней, без поиска
]


def fav_key(artist: str, title: str) -> str:
    """Один и тот же трек получает один ключ при любом регистре и знаках препинания."""
    return re.sub(r"[\W_]+", "", f"{artist}|{title}".lower())


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cur = await db.execute(f"PRAGMA table_info({table})")
            if column not in [row[1] for row in await cur.fetchall()]:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        await db.commit()


async def get_profile(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT display_name, avatar_token, bg_token FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
    return {
        "name": (row[0] or "") if row else "",
        "avatar_token": (row[1] or "") if row else "",
        "bg_token": (row[2] or "") if row else "",
    }


async def _ensure_user(db, user_id: int) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO users(user_id, name, first_seen) VALUES (?,?,?)", (user_id, "", int(time.time()))
    )


async def set_display_name(user_id: int, name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_user(db, user_id)
        await db.execute("UPDATE users SET display_name=? WHERE user_id=?", (name, user_id))
        await db.commit()


async def set_bg_token(user_id: int, token: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_user(db, user_id)
        await db.execute("UPDATE users SET bg_token=? WHERE user_id=?", (token, user_id))
        await db.commit()


async def set_avatar_token(user_id: int, token: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_user(db, user_id)
        await db.execute("UPDATE users SET avatar_token=? WHERE user_id=?", (token, user_id))
        await db.commit()


async def admin_overview() -> dict:
    """Сводка для админки: общие числа и список всех пользователей."""
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:

        async def one(sql, *args):
            cur = await db.execute(sql, args)
            return (await cur.fetchone())[0] or 0

        totals = {
            "users": await one(
                "SELECT COUNT(*) FROM (SELECT user_id FROM users UNION SELECT user_id FROM plays"
                " UNION SELECT user_id FROM favorites)"
            ),
            "plays": await one("SELECT COUNT(*) FROM plays"),
            "plays_24h": await one("SELECT COUNT(*) FROM plays WHERE ts>?", now - 86400),
            "plays_7d": await one("SELECT COUNT(*) FROM plays WHERE ts>?", now - 7 * 86400),
            "active_7d": await one("SELECT COUNT(DISTINCT user_id) FROM plays WHERE ts>?", now - 7 * 86400),
            "favorites": await one("SELECT COUNT(*) FROM favorites"),
        }
        cur = await db.execute(
            "SELECT i.user_id, u.name, u.display_name, u.first_seen, u.avatar_token, u.bg_token,"
            " (SELECT COUNT(*) FROM plays p WHERE p.user_id=i.user_id),"
            " (SELECT MAX(ts) FROM plays p WHERE p.user_id=i.user_id),"
            " (SELECT COUNT(*) FROM favorites f WHERE f.user_id=i.user_id)"
            " FROM (SELECT user_id FROM users UNION SELECT user_id FROM plays UNION SELECT user_id FROM favorites) i"
            " LEFT JOIN users u ON u.user_id=i.user_id ORDER BY 8 DESC, u.first_seen DESC"
        )
        users = [
            {
                "id": i,
                "tg_name": n or "",
                "name": dn or "",
                "first_seen": fs or 0,
                "avatar": av or "",
                "bg": bg or "",
                "plays": pc,
                "last": last or 0,
                "favorites": fc,
            }
            for i, n, dn, fs, av, bg, pc, last, fc in await cur.fetchall()
        ]
        cur = await db.execute(
            "SELECT title, artist, COUNT(*) c FROM plays GROUP BY LOWER(title), LOWER(artist) ORDER BY c DESC LIMIT 10"
        )
        top = [{"title": t or "", "artist": a or "", "plays": c} for t, a, c in await cur.fetchall()]
    return {"totals": totals, "users": users, "top_tracks": top}


async def admin_user(user_id: int) -> dict:
    """Подробности одного пользователя: последние прослушивания и избранное."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT title, artist, source, kind, ts FROM plays WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,)
        )
        plays = [
            {"title": t or "", "artist": a or "", "source": s or "", "kind": k or "", "ts": ts or 0}
            for t, a, s, k, ts in await cur.fetchall()
        ]
    return {"plays": plays, "favorites": await fav_list(user_id, 100)}


FAV_LIMIT = 2000  # после добавления плейлистов избранное бывает большим; список не должен молча обрезаться


async def fav_list(user_id: int, limit: int = FAV_LIMIT) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, key, title, artist, duration, cover, url FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
    return [
        {"id": i, "key": k, "title": t, "artist": a, "duration": d or 0, "cover": c or "", "url": u or ""}
        for i, k, t, a, d, c, u in rows
    ]


async def fav_has(user_id: int, artist: str, title: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM favorites WHERE user_id=? AND key=?", (user_id, fav_key(artist, title)))
        return await cur.fetchone() is not None


async def fav_add(user_id: int, artist: str, title: str, duration: int = 0, cover: str = "", url: str = "") -> None:
    await fav_add_many(user_id, [{"artist": artist, "title": title, "duration": duration, "cover": cover, "url": url}])


async def fav_add_many(user_id: int, items: list[dict]) -> int:
    """Добавляет пачку песен одной транзакцией (плейлист). Уже добавленные пропускаются. Возвращает, сколько новых."""
    now = int(time.time())
    rows = [
        (
            user_id,
            fav_key(it["artist"], it["title"]),
            it["title"],
            it["artist"],
            it.get("duration") or 0,
            it.get("cover") or "",
            it.get("url") or "",
            now,
        )
        # список избранного идёт от новых к старым, поэтому вставляем с конца: первая песня плейлиста окажется сверху
        for it in reversed(items)
        if it.get("title")
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        before = db.total_changes
        await db.executemany(
            "INSERT OR IGNORE INTO favorites(user_id, key, title, artist, duration, cover, url, ts) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        await db.commit()
        return db.total_changes - before


async def fav_remove(user_id: int, artist: str, title: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM favorites WHERE user_id=? AND key=?", (user_id, fav_key(artist, title)))
        await db.commit()


async def get_play(play_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, title, artist, duration, cover FROM plays WHERE id=?", (play_id,))
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "user_id": row[0],
        "title": row[1] or "",
        "artist": row[2] or "",
        "duration": row[3] or 0,
        "cover": row[4] or "",
    }


async def touch_user(user_id: int, name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, name, first_seen) VALUES (?,?,?)",
            (user_id, name, int(time.time())),
        )
        await db.commit()


async def get_cached(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT file_id, title, artist, duration FROM cache WHERE key=?", (key,))
        return await cur.fetchone()


async def set_cached(key: str, file_id: str, title: str, artist: str, duration: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO cache(key, file_id, title, artist, duration) VALUES (?,?,?,?,?)",
            (key, file_id, title, artist, duration),
        )
        await db.commit()


async def get_search(key: str, max_age: int) -> str | None:
    """Сохранённая выдача поиска (JSON), если она не старше max_age секунд. Переживает перезапуск бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT data FROM search_cache WHERE key=? AND ts>=?", (key, int(time.time()) - max_age))
        row = await cur.fetchone()
    return row[0] if row else None


async def set_search(key: str, data: str, keep: int = 5000) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO search_cache(key, ts, data) VALUES (?,?,?)", (key, int(time.time()), data)
        )
        # таблица не растёт бесконечно: оставляем самые свежие записи
        await db.execute(
            "DELETE FROM search_cache WHERE key NOT IN (SELECT key FROM search_cache ORDER BY ts DESC LIMIT ?)",
            (keep,),
        )
        await db.commit()


async def log_play(
    user_id: int, title: str, artist: str, duration: int, source: str, url: str, kind: str, cover: str = ""
) -> int:
    """Записывает прослушивание и возвращает его id (на него ссылается кнопка «в избранное»)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO plays(user_id, title, artist, duration, source, url, kind, ts, cover) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, title, artist, duration, source, url, kind, int(time.time()), cover),
        )
        await db.commit()
        return cur.lastrowid


async def dashboard(user_id: int) -> dict:
    """Всё для мини-аппа: итоги, активность по дням и часам, топы, последние треки."""
    async with aiosqlite.connect(DB_PATH) as db:

        async def rows(sql, *args):
            cur = await db.execute(sql, args)
            return await cur.fetchall()

        total, seconds, first_ts = (
            await rows("SELECT COUNT(*), COALESCE(SUM(duration),0), MIN(ts) FROM plays WHERE user_id=?", user_id)
        )[0]
        days = await rows(
            "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, COUNT(*) "
            "FROM plays WHERE user_id=? AND ts >= strftime('%s','now','-60 day') GROUP BY d",
            user_id,
        )
        hours = await rows(
            "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) h, COUNT(*) "
            "FROM plays WHERE user_id=? GROUP BY h",
            user_id,
        )
        top_artists = await rows(
            "SELECT artist, COUNT(*) c, COALESCE(SUM(duration),0) FROM plays "
            "WHERE user_id=? AND artist != '' GROUP BY artist ORDER BY c DESC, 3 DESC LIMIT 8",
            user_id,
        )
        # ссылка, длина и обложка — чтобы песню из списка можно было сразу включить в мини-аппе
        top_tracks = await rows(
            "SELECT title, artist, COUNT(*) c, MAX(duration), MAX(COALESCE(url, '')), MAX(COALESCE(cover, '')) "
            "FROM plays WHERE user_id=? GROUP BY title, artist ORDER BY c DESC LIMIT 8",
            user_id,
        )
        recent = await rows(
            "SELECT title, artist, duration, ts, COALESCE(url, ''), COALESCE(cover, '') FROM plays WHERE user_id=? "
            "ORDER BY id DESC LIMIT 12",
            user_id,
        )
        sources = await rows(
            "SELECT source, COUNT(*) FROM plays WHERE user_id=? GROUP BY source ORDER BY 2 DESC", user_id
        )
    return {
        "total": total,
        "seconds": seconds,
        "first_ts": first_ts,
        "days": {d: c for d, c in days},
        "hours": {h: c for h, c in hours},
        "top_artists": [{"artist": a, "plays": c, "seconds": s} for a, c, s in top_artists],
        "top_tracks": [
            {"title": t, "artist": a, "plays": c, "duration": d or 0, "url": u, "cover": cv}
            for t, a, c, d, u, cv in top_tracks
        ],
        "recent": [
            {"title": t, "artist": a, "duration": d or 0, "ts": ts, "url": u, "cover": cv}
            for t, a, d, ts, u, cv in recent
        ],
        "sources": [{"source": s, "plays": c} for s, c in sources],
    }


async def top_artist_names(user_id: int, limit: int = 3) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT artist FROM plays WHERE user_id=? AND artist != '' GROUP BY artist ORDER BY COUNT(*) DESC LIMIT ?",
            (user_id, limit),
        )
        return [r[0] for r in await cur.fetchall()]


async def taste_artists(user_id: int, limit: int = 20) -> list[tuple[str, int]]:
    """Исполнители пользователя с весом для «Моей волны»: прослушивание весит 1, песня в избранном — 3."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT artist, SUM(w) FROM ("
            " SELECT artist, 1 AS w FROM plays WHERE user_id=? AND artist != ''"
            " UNION ALL SELECT artist, 3 AS w FROM favorites WHERE user_id=? AND artist != ''"
            ") GROUP BY LOWER(artist) ORDER BY 2 DESC LIMIT ?",
            (user_id, user_id, limit),
        )
        return [(a, int(w)) for a, w in await cur.fetchall()]


async def user_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*), COALESCE(SUM(duration),0) FROM plays WHERE user_id=?", (user_id,))
        total, seconds = await cur.fetchone()
        cur = await db.execute(
            "SELECT artist, COUNT(*) c FROM plays WHERE user_id=? AND artist != '' "
            "GROUP BY artist ORDER BY c DESC LIMIT 5",
            (user_id,),
        )
        top_artists = await cur.fetchall()
        cur = await db.execute(
            "SELECT title, artist, COUNT(*) c FROM plays WHERE user_id=? "
            "GROUP BY title, artist ORDER BY c DESC LIMIT 5",
            (user_id,),
        )
        top_tracks = await cur.fetchall()
    return {
        "total": total,
        "seconds": seconds,
        "top_artists": top_artists,
        "top_tracks": top_tracks,
    }
