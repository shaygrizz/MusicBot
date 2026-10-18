import re

from aiohttp.test_utils import TestClient, TestServer
from test_auth import TOKEN, init_data

from musicbot import db, texts
from musicbot.web.app import make_app

FIELD = re.compile(r"\{(\w+)(?::[^}]*)?\}")


def test_every_language_has_every_message():
    for code, table in texts.LINES.items():
        assert table.keys() == texts.EN.keys(), f"{code}: {set(texts.EN) ^ set(table)}"


def test_translations_keep_the_placeholders():
    for code, table in texts.LINES.items():
        for key, line in table.items():
            assert set(FIELD.findall(line)) == set(FIELD.findall(texts.EN[key])), f"{code}.{key}"


def test_every_message_formats():
    for code in texts.LANGS:
        for key in texts.EN:
            fields = {name: 1 for name in FIELD.findall(texts.EN[key])}
            assert texts.say(key, code, **fields)


def test_say_follows_the_language_of_the_current_update():
    try:
        texts.use("ru")
        assert texts.say("pl_stop") == texts.RU["pl_stop"]
        texts.use("it")
        assert texts.say("pl_stop") == texts.IT["pl_stop"]
        texts.use("xx")  # unknown -> English
        assert texts.say("pl_stop") == texts.EN["pl_stop"]
    finally:
        texts.use(texts.DEFAULT)


def test_guess_takes_telegram_interface_language():
    assert texts.guess("ru") == "ru"
    assert texts.guess("it-IT") == "it"
    assert texts.guess("de") == "en"
    assert texts.guess(None) == "en"


def test_reply_keyboard_buttons_are_recognised_in_every_language():
    assert {texts.RU["kb_favorites"], texts.IT["kb_favorites"], texts.EN["kb_favorites"]} <= set(
        texts.variants("kb_favorites")
    )


async def test_language_is_saved_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()
    assert await db.get_lang(7) is None
    await db.set_lang(7, "it")
    assert await db.get_lang(7) == "it"
    await db.set_lang(7, "ru")
    assert await db.get_lang(7) == "ru"
    assert await db.get_lang(8) is None


def signed(user_id=42, language_code="ru"):
    return init_data(user={"id": user_id, "first_name": "Rese", "language_code": language_code})


async def test_mini_app_saves_and_reports_the_language(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()
    c = TestClient(TestServer(make_app(None, TOKEN, "test_bot", set())))
    await c.start_server()
    try:
        head = {"X-Init-Data": signed()}
        first = await (await c.get("/api/dashboard", headers=head)).json()
        assert first["profile"]["lang"] == "ru"  # nothing chosen yet: Telegram's language
        assert (await c.post("/api/lang", json={"lang": "klingon"}, headers=head)).status == 400
        assert (await c.post("/api/lang", json={"lang": "it"}, headers=head)).status == 200
        assert await db.get_lang(42) == "it"
        again = await (await c.get("/api/dashboard", headers=head)).json()
        assert again["profile"]["lang"] == "it"
        assert (await c.post("/api/lang", json={"lang": "it"})).status == 401
    finally:
        await c.close()


def test_page_has_the_same_words_for_every_language():
    from musicbot.web.app import INDEX

    page = INDEX.read_text(encoding="utf-8")
    block = page[page.index("const I18N = {") : page.index("const guessLang")]
    sections = re.split(r"\n  (en|ru|it): \{\n", block)[1:]
    keys = {}
    for code, body in zip(sections[::2], sections[1::2]):
        bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', body)  # keys only, not the words inside the texts
        keys[code] = set(re.findall(r"(?:^|[\s,{])(\w+): ", bare)) - {"one", "few", "many", "other"}
    assert keys["en"] and keys["en"] == keys["ru"] == keys["it"]
    used = set(re.findall(r'\bt\("(\w+)"', page)) | set(re.findall(r'\btn\("(\w+)"', page))
    used |= set(re.findall(r'\["\w+", "(tab\w+)"\]', page))
    assert used - keys["en"] == set()
