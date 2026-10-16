"""Кому можно пользоваться ботом: ALLOWED_USERS в .env (пусто — всем)."""

import os

ALLOWED = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x}


def allowed(user_id: int) -> bool:
    return not ALLOWED or user_id in ALLOWED
