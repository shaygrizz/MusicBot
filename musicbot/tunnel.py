"""Публичный HTTPS-адрес для мини-аппа через Cloudflare Quick Tunnel (без аккаунта и домена).

Нужен файл cloudflared.exe в tools/cloudflared/ или в PATH. Адрес меняется при каждом запуске,
поэтому бот каждый раз заново прописывает его в кнопке меню."""

import asyncio
import logging
import re
import shutil
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
LOCAL_EXE = Path(__file__).parent.parent / "tools" / "cloudflared" / "cloudflared.exe"


def find_exe() -> str | None:
    return str(LOCAL_EXE) if LOCAL_EXE.exists() else shutil.which("cloudflared")


async def start(port: int, timeout: float = 40) -> tuple[asyncio.subprocess.Process, str] | None:
    exe = find_exe()
    if not exe:
        return None
    proc = await asyncio.create_subprocess_exec(
        exe,
        "tunnel",
        "--url",
        f"http://127.0.0.1:{port}",
        "--no-autoupdate",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    found: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def drain():  # читаем вывод всё время, иначе канал переполнится и туннель встанет
        async for raw in proc.stdout:
            m = URL_RE.search(raw.decode(errors="ignore"))
            if m and not found.done():
                found.set_result(m.group(0))

    asyncio.create_task(drain())
    try:
        return proc, await asyncio.wait_for(found, timeout)
    except TimeoutError:
        logging.warning("туннель не поднялся за %s с", timeout)
        proc.terminate()
        return None
