"""Поиск музыки: источники, каталоги, точный трек, поиск по тексту, версии песни, скачивание с автоповтором."""

from .engine import search
from .fetch import fetch_audio, resolve
from .models import ICONS, Canon, Outcome, lazy_for
from .versions import versions
from .warm import keep_warm

__all__ = ["Canon", "ICONS", "Outcome", "fetch_audio", "keep_warm", "lazy_for", "resolve", "search", "versions"]
