"""Общие правила разбора названий: нормализация строк и признаки «не оригинала»."""

import re
import unicodedata
from difflib import SequenceMatcher


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


_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "i", "є": "e", "ґ": "g",
    }
)  # fmt: skip


def latin(s: str) -> str:
    """Кириллица латиницей для сравнения имён: «Кино» -> «kino», «Земфира» -> «zemfira»."""
    return norm(s).translate(_TRANSLIT)


def same_artist(a: str, b: str) -> bool:
    """Один и тот же исполнитель, даже если в каталоге он латиницей, а на YouTube кириллицей
    («Zemfira» и «Земфира», «Splean» и «Сплин»). Без транслита такие песни не находились."""
    x, y = latin(a), latin(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if min(len(x), len(y)) >= 4 and (x in y or y in x):  # «u2» не должно совпадать с «theu2tributeband»
        return True
    return min(len(x), len(y)) >= 5 and SequenceMatcher(None, x, y).ratio() >= 0.7  # короткие имена — только точно


# хвосты каталогов, которые запись не меняют: «Draw You Out - From The Hunger Games: …», «Song - Remastered 2011»,
# «Song - Radio Edit», «Stateside + Zara Larsson». На YouTube такая песня называется короче, и без обрезки
# точное совпадение не находится. «- Acoustic Version», «- Live» и т.п. не трогаем: это другие записи
_CATALOG_SUFFIX_RE = re.compile(
    r"\s+[-–—]\s+(?:from\b|(?:\d{4}\s+)?remaster|radio\s+edit|single\s+version|album\s+version|original\s+version|"
    r"mono\b|stereo\b|bonus\s+track|edit\b|mixed\b|mix\s+cut\b|recorded\s+at\b|spotify\s+(?:singles|sessions)\b).*$",
    re.I,
)
# «Song - Mixed», «Song - Mix Cut»: версия из альбома-микса, обрезанная под переходы между песнями
_MIX_CUT_RE = re.compile(r"\s+[-–—]\s+(?:mixed\b|mix\s+cut\b)", re.I)


def is_mix_cut(title: str) -> bool:
    """Длина такой версии в каталоге — длина куска в миксе, а не песни: сравнивать её с YouTube нельзя."""
    return bool(_MIX_CUT_RE.search(title))


_CATALOG_PLUS_RE = re.compile(r"\s+\+\s+\S.*$")


def core_title(title: str) -> str:
    """Название песни без пометок каталога после тире или «+»; если отрезать нечего — как было."""
    cut = _CATALOG_PLUS_RE.sub("", _CATALOG_SUFFIX_RE.sub("", title)).strip()
    return cut or title


# версии, которые не оригинал: ремиксы, live, каверы, замедленные и т.п.
BAD_VERSION_RE = re.compile(
    r"\b(skip\w*|slowed|sped\s*up|speed\s*up|nightcore|reverb|8d|bass\s*boost(?:ed)?|karaoke|"
    r"instrumental|covers?|live|remix|mashup|loop|hours?|preview|snippet|a\s*cappella|acapella|fingerstyle|"
    r"lullaby|arrangement|choreography|tutorial|reaction|parody|piano\s+(?:version|cover|ver\.?)|"
    r"кавер\w*|ремикс\w*|караоке|замедл\w*|ускор\w*)\b",
    re.I,
)
