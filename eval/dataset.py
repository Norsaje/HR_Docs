"""Адаптер открытого набора `data/open/` для оценки качества (только eval, не пайплайн).

Набор разложен по папкам-«пакетам», вид документа указан в имени файла. Здесь имя файла
превращается в МЕТКУ ЭТАЛОНА — ровно как разметка человеком. В приложение этот модуль
не импортируется: пайплайн получает только байты и UUID (AGENTS.md, правило 1).
Ключ образца — sha256 содержимого, как в eval/gold/.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPEN_DIR = ROOT / "data" / "open"

# Префикс имени файла → (вид документа в наборе, сторона). Порядок важен: длинные префиксы раньше.
_LABELS: list[tuple[str, str, str]] = [
    (
        "Паспорт РФ - страница прописки",
        "ru_passport_registration",
        "other",
    ),  # не тот разворот
    ("Паспорт РФ", "ru_passport", "main_spread"),
    ("СНИЛС", "snils", "front"),
    ("ИНН", "inn", "front"),
    ("Патент (лицо)", "work_patent", "front"),
    ("Патент (оборот)", "work_patent", "back"),
    ("ВУ РФ (лицо)", "ru_driver_license", "front"),
    ("ВУ РФ (оборот)", "ru_driver_license", "back"),
    ("ВУ Киргизии", "kg_driver_license", "front"),
    ("Паспорт Беларуси", "by_passport", "main_spread"),
    ("Паспорт Индии", "in_passport", "main_spread"),
    ("Чек", "not_document", "other"),
    ("Размытое фото", "unreadable_blurry", "other"),
    ("Тёмное фото", "unreadable_dark", "other"),
]

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}


@dataclass(frozen=True)
class Sample:
    path: Path
    sha256: str
    kind: str  # вид документа в наборе (метка эталона)
    side: str

    @property
    def short(self) -> str:
        """Идентификатор для отчётов: префикс хеша, без имени файла (в имени — ПДн)."""
        return self.sha256[:10]


def label_for(name: str) -> tuple[str, str]:
    for prefix, kind, side in _LABELS:
        if name.startswith(prefix):
            return kind, side
    return "unknown", "other"


def load_samples(open_dir: Path = OPEN_DIR) -> list[Sample]:
    samples = []
    seen: set[str] = set()
    for path in sorted(open_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha in seen:  # точная копия файла — один образец
            continue
        seen.add(sha)
        kind, side = label_for(path.name)
        samples.append(Sample(path, sha, kind, side))
    return samples


if __name__ == "__main__":
    from collections import Counter

    s = load_samples()
    print(f"образцов: {len(s)}")
    for (kind, side), n in sorted(Counter((x.kind, x.side) for x in s).items()):
        print(f"  {kind:26} {side:12} {n}")
