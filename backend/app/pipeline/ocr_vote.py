"""Голос второго независимого распознавателя (olmOCR, проход B) за значение поля.

Из markdown OCR достаём кандидатов: последовательности цифр (с пробелами и дефисами), даты,
слова в верхнем регистре. Значение Qwen «подтверждено», если оно есть среди кандидатов:
цифры и даты — точно, имена — каждое слово точно. Если точного нет, но есть близкий кандидат
того же вида (Левенштейн ≤ 2, та же длина) — это расхождение: OCR прочитал иначе.
Если ничего похожего нет — OCR «промолчал», и подтвердить значение нечем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.registry.loader import FieldDef
from app.registry.rules import parse_date

_DATE_LIKE = re.compile(
    r"\d{1,2}[./\- ]\d{1,2}[./\- ]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s*[A-Za-zА-Яа-я]{3,}[^\d\n]{0,12}\d{2,4}"
)


@dataclass(frozen=True)
class OcrCandidates:
    digits: set[str]
    dates: set[str]
    words: set[str]
    compact: str  # весь текст в верхнем регистре без пробелов — для кодов вроде AB1234567


def candidates(markdown: str) -> OcrCandidates:
    text = markdown.replace("&lt;", "<")
    digits = {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d \-]*\d|\d", text)}
    dates = set()
    for m in _DATE_LIKE.findall(text):
        for role in ("birth", "expiry"):
            d = parse_date(m, role)
            if d:
                dates.add(d.isoformat())
    upper = text.upper().replace("Ё", "Е")
    words = set(re.findall(r"[A-ZА-Я0-9']+", upper.replace("’", "'").replace("ʻ", "'")))
    return OcrCandidates(digits, dates, words, re.sub(r"\s+", "", upper))


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def vote(f: FieldDef, value: str, c: OcrCandidates, votes_cyrillic: bool = True) -> tuple[str, str | None]:
    """(сигнал, ближайший кандидат OCR): ocr_agree | ocr_disagree | ocr_silent | ocr_no_vote."""
    if f.type == "digits":
        if value in c.digits:
            return "ocr_agree", None
        near = [d for d in c.digits if len(d) == len(value) and levenshtein(d, value) <= 2]
        return ("ocr_disagree", near[0]) if near else ("ocr_silent", None)
    if f.type == "date":
        if value in c.dates:
            return "ocr_agree", None
        near = [d for d in c.dates if levenshtein(d, value) <= 2]
        return ("ocr_disagree", near[0]) if near else ("ocr_silent", None)
    if f.type in ("name", "latin_name"):
        if re.search(r"[А-Яа-я]", value) and not votes_cyrillic:
            return "ocr_no_vote", None
        tokens = [t for t in re.split(r"[\s\-]+", value) if t]
        if tokens and all(t in c.words for t in tokens):
            return "ocr_agree", None
        return "ocr_silent", None  # для имён расхождение не объявляем: OCR часто режет слова
    if f.type == "code":
        # Короткий код (серия «77», «UZB») найдётся в любом тексте — такое совпадение ничего не доказывает.
        return ("ocr_agree", None) if len(value) >= 5 and value in c.compact else ("ocr_silent", None)
    return "ocr_no_vote", None  # свободный текст и enum вторым голосом не подтверждаем
