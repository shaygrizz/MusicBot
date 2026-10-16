from aiohttp.test_utils import TestClient, TestServer

from musicbot.web.app import make_app


async def client():
    app = make_app(None, "123456:TEST-TOKEN", "test_bot", set())
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def test_health_and_static_files_are_served():
    c = await client()
    try:
        assert (await c.get("/healthz")).status == 200
        page = await c.get("/")
        assert page.status == 200
        assert "<html" in (await page.text()).lower()
        assert (await c.get("/static/Diamond.jpg")).status == 200
    finally:
        await c.close()


async def test_api_requires_signed_telegram_data():
    c = await client()
    try:
        assert (await c.get("/api/dashboard")).status == 401
        assert (await c.post("/api/favorites", json={})).status == 401
        assert (await c.get("/api/recs", headers={"X-Init-Data": "hash=broken"})).status == 401
    finally:
        await c.close()


async def test_admin_api_is_hidden_from_everyone_else():
    c = await client()
    try:
        assert (await c.get("/api/admin")).status == 404
        assert (await c.get("/api/admin/user?id=1")).status == 404
    finally:
        await c.close()


async def test_unknown_file_tokens_are_not_found():
    c = await client()
    try:
        assert (await c.get("/fav/" + "0" * 32)).status in (401, 404)
        assert (await c.get("/fav/..%2F..%2Fsecret")).status in (401, 404)
    finally:
        await c.close()


async def test_stream_link_needs_a_valid_token():
    c = await client()
    try:
        assert (await c.get("/api/stream/not-a-real-token")).status == 404
    finally:
        await c.close()
