"""Общие правила разбора названий: нормализация строк и признаки «не оригинала»."""

import re
import unicodedata


def fold(s: str) -> str:
    """«São Paulo» -> «Sao Paulo»: убираем диакритику у латиницы (у кириллицы «й», «ё» не трогаем)."""
    out = []
    for ch in s:
        d = unicodedata.normalize("NFD", ch)
        out.append(d[0] if len(d) > 1 and d[0].isascii() else ch)
    return "".join(out)


def norm(s: str) -> str:
    """Название для сравнения: без скобок, регистра, диакритики, знаков и пробелов."""
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", fold(s.lower()))
    return re.sub(r"[\W_]+", "", s)


# версии, которые не оригинал: ремиксы, live, каверы, замедленные и т.п.
BAD_VERSION_RE = re.compile(
    r"\b(skip\w*|slowed|sped\s*up|speed\s*up|nightcore|reverb|8d|bass\s*boost(?:ed)?|karaoke|"
    r"instrumental|covers?|live|remix|mashup|loop|hours?|preview|snippet|a\s*cappella|acapella|fingerstyle|"
    r"lullaby|arrangement|choreography|tutorial|reaction|parody|piano\s+(?:version|cover|ver\.?)|"
    r"кавер\w*|ремикс\w*|караоке|замедл\w*|ускор\w*)\b",
    re.I,
)
