"""Нормализация значений и правила реестра (закрытый набор проверок из documents.yaml).

Нормализация по типу поля: digits — только цифры; date — ISO; name — верхний регистр, Ё→Е;
code — без пробелов. Правило получает нормализованные значения всех полей документа и
возвращает текст замечания или None. Детерминированный код, без модели: его результат
решает цвет поля. Неизвестная проверка не «проходит молча» — загрузчик реестра её не пустит.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.registry.checksums import inn12_ok, snils_ok
from app.registry.loader import DocumentDef, FieldDef, Registry, RuleDef

_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "мая": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def today(tz: str = "Europe/Moscow") -> date:
    """«Сегодня» в часовом поясе стенда; без базы часовых поясов (Windows) — UTC+3."""
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(timezone(timedelta(hours=3))).date()


def parse_date(raw: str, role: str | None = None, now: date | None = None) -> date | None:
    """Дата из распространённых форматов; None — не распознана или некорректна (31.02)."""
    s = raw.strip().lower().replace("«", "").replace("»", "").replace("г.", "").strip()
    now = now or today()
    try:
        if m := re.fullmatch(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})", s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})[./\- ](\d{1,2})[./\- ](\d{2}|\d{4})", s):
            d, mo, y = int(m[1]), int(m[2]), m[3]
        elif m := re.search(r"(\d{1,2})\s*([a-zа-я]{3})[a-zа-я]*(?:\s*/\s*[a-zа-я]+)?\.?\s*(\d{2}|\d{4})", s):
            d, mo, y = int(m[1]), _MONTHS.get(m[2], 0), m[3]
        else:
            return None
        year = int(y)
        if len(y) == 2:
            # Век: для рождения и выдачи — дата не в будущем; для сроков — 20YY.
            year = 2000 + year if role == "expiry" or 2000 + year <= now.year else 1900 + year
        return date(year, mo, d)
    except ValueError:
        return None


def normalize(f: FieldDef, raw: str | None) -> str | None:
    if raw is None:
        return None
    if f.type == "digits":
        return re.sub(r"\D", "", raw) or None
    if f.type == "date":
        d = parse_date(raw, f.date_role)
        return d.isoformat() if d else raw.strip()
    if f.type in ("name", "latin_name"):
        return re.sub(r"\s+", " ", raw.upper().replace("Ё", "Е").replace("’", "'").replace("ʻ", "'")).strip()
    if f.type == "code":
        return re.sub(r"\s+", "", raw.upper())
    return re.sub(r"\s+", " ", raw).strip()


def _d(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def check(rule: RuleDef, values: dict[str, str | None], registry: Registry, warn_days: int) -> str | None:
    """Текст замечания, если правило нарушено; None — пройдено или нечего проверять."""
    v = values.get(rule.field)
    if v is None:
        return None
    other = values.get(rule.other) if rule.other else None
    now = today(registry.timezone)
    c = rule.check
    if c == "length":
        n = len(v)
        bad = (
            (rule.value is not None and n != int(rule.value))
            or (rule.min is not None and n < rule.min)
            or (rule.max is not None and n > rule.max)
        )
        return rule.message if bad else None
    if c == "pattern":
        return None if re.fullmatch(str(rule.value), v) else rule.message
    if c == "checksum_inn12":
        return None if inn12_ok(v) else rule.message
    if c == "checksum_snils":
        return None if snils_ok(v) else rule.message
    if c == "latin_only":
        return None if not re.search(r"[А-Яа-яЁё]", v) else rule.message
    if c == "cyrillic_only":
        return None if not re.search(r"[A-Za-z]", v) else rule.message
    if c == "in_values":
        return rule.message if v == "other" else None
    if c == "iso_alpha3":
        return None if v in registry.eu_alpha3 else rule.message.replace("{value}", v)
    dv = _d(v)
    if dv is None:
        return "Некорректная дата — проверьте распознавание"
    od = _d(other)
    if c == "not_in_future":
        return rule.message if dv > now else None
    if c == "not_expired":
        return rule.message.replace("{date}", dv.strftime("%d.%m.%Y")) if dv < now else None
    if c == "expires_within":
        days = (dv - now).days
        return (
            rule.message.replace("{date}", dv.strftime("%d.%m.%Y")).replace("{days}", str(days))
            if 0 <= days <= warn_days
            else None
        )
    if od is None:
        return None
    if c == "after_field":
        return rule.message if dv < od else None
    if c == "before_field":
        return rule.message if dv > od else None
    if c == "max_months_after":
        months = (dv.year - od.year) * 12 + dv.month - od.month
        return rule.message if months > int(rule.value) else None
    if c == "min_age_at_issue":
        age = dv.year - od.year - ((dv.month, dv.day) < (od.month, od.day))
        return rule.message if age < int(rule.value) else None
    if c == "ru_passport_age_validity":
        # Выдан до 20 лет — действует до 20; до 45 — до 45 (постановление № 828).
        age_now = now.year - od.year - ((now.month, now.day) < (od.month, od.day))
        age_issue = dv.year - od.year - ((dv.month, dv.day) < (od.month, od.day))
        for limit in (20, 45):
            if age_issue < limit <= age_now:
                return rule.message.replace("{age}", str(limit))
        return None
    return None


def evaluate(
    doc: DocumentDef, values: dict[str, str | None], registry: Registry, warn_days: int
) -> list[tuple[RuleDef, str]]:
    return [(r, msg) for r in doc.rules if (msg := check(r, values, registry, warn_days))]
