"""Загрузка версионированных промптов из `prompts/<id>.<version>.md`.

Файл: YAML-frontmatter (параметры модели, список плейсхолдеров, схема) и секции
`## system`, `## user`, `## retry_on_schema_error`. HTML-комментарии — заметки для людей,
в модель не уходят. Подстановка — только объявленных плейсхолдеров `{name}`:
str.format не подходит, потому что в тексте промптов есть фигурные скобки JSON.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SECTION = re.compile(r"^## (\w+)\s*$", re.M)


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    model_params: dict[str, Any]
    placeholders: list[str]
    sections: dict[str, str]
    schema: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def render(self, section: str, **values: str) -> str:
        text = self.sections[section]
        for name, value in values.items():
            text = text.replace("{" + name + "}", value)
        return text


def parse_prompt(text: str) -> Prompt:
    text = text.replace("\r\n", "\n")
    m = _FRONTMATTER.match(text)
    if not m:
        raise ValueError("в промпте нет frontmatter между строками ---")
    meta = yaml.safe_load(m.group(1)) or {}
    body = _COMMENT.sub("", m.group(2))
    parts = _SECTION.split(body)
    # parts = [преамбула, имя1, текст1, имя2, текст2, ...]
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if "system" not in sections or "user" not in sections:
        raise ValueError("в промпте должны быть секции ## system и ## user")
    return Prompt(
        id=str(meta["id"]),
        version=str(meta["version"]),
        model_params=dict(meta.get("model_params") or {}),
        placeholders=list(meta.get("placeholders") or []),
        sections=sections,
        schema=meta.get("schema"),
        meta=meta,
    )


@lru_cache
def load_prompt(prompts_dir: Path, prompt_id: str, version: str) -> Prompt:
    path = prompts_dir / f"{prompt_id}.{version}.md"
    prompt = parse_prompt(path.read_text(encoding="utf-8"))
    if prompt.version != version:
        raise ValueError(
            f"{path.name}: version во frontmatter ({prompt.version}) не совпадает с именем файла"
        )
    return prompt
