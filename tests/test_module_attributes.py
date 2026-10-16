"""Каждое обращение «модуль.имя» в коде должно указывать на то, что в модуле есть.
Такие ошибки (download.DOWNLOAD_DIR) обычные тесты не ловят: ветка запускается только живым сообщением в Telegram."""

import ast
import importlib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "musicbot"
FILES = sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def imported_modules(tree: ast.Module, path: Path) -> dict[str, str]:
    """Имена, под которыми файл импортировал модули пакета: from . import db -> {"db": "musicbot.db"}."""
    package = ".".join(("musicbot", *path.relative_to(PACKAGE).parent.parts)).rstrip(".")
    found = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
            target = f"{base}.{node.module}" if node.module else base
            for alias in node.names:
                full = f"{target}.{alias.name}"
                try:
                    importlib.import_module(full)
                except ImportError:
                    continue  # импортировали функцию или класс, а не модуль
                found[alias.asname or alias.name] = full
    return found


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_attributes_used_on_package_modules_exist(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = imported_modules(tree, path)
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            if isinstance(node.ctx, ast.Load) and not hasattr(
                importlib.import_module(modules[node.value.id]), node.attr
            ):
                missing.append(f"{node.value.id}.{node.attr} (строка {node.lineno})")
    assert not missing, f"в {path.name} нет таких имён: {', '.join(missing)}"
