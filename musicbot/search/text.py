"""Разбор запроса пользователя: чистка, слова, стоп-слова."""

import re

from ..textutil import norm

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "to",
    "in",
    "on",
    "my",
    "me",
    "you",
    "your",
    "it",
    "is",
    "im",
    "i",
    "we",
    "he",
    "she",
    "for",
    "with",
    "at",
    "be",
    "no",
    "ft",
    "feat",
    "и",
    "в",
    "на",
    "я",
    "ты",
    "не",
    "что",
    "как",
    "то",
    "мне",
    "тебя",
    "с",
}


NOISE_RE = re.compile(
    r"\b(скачать|скачай|download|mp3|песня|песню|трек|музыка|музыку|найди|найти|free|бесплатно)\b",
    re.I,
)


def clean_query(q: str) -> str:
    q = NOISE_RE.sub(" ", q)
    q = re.sub(r"[\"“”«»]", " ", q)
    return re.sub(r"\s+", " ", q).strip(" -–—.,!?")


def query_tokens(query: str) -> list[str]:
    return [t for t in (norm(w) for w in query.split()) if len(t) >= 2]
