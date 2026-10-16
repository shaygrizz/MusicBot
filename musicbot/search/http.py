"""Запросы к открытым JSON API (общая сессия с keep-alive живёт в musicbot.net)."""

from ..net import get_bytes, get_json, session

__all__ = ["get_bytes", "get_json", "session"]
