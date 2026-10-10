"""Свой парсер машиночитаемой зоны (ICAO 9303): TD3 (паспорт, 2×44), TD1 (ID-карта, 3×30)
и формат внутреннего паспорта РФ (2×44). Без AGPL-библиотек вроде mrz.

МЧЗ — «твёрдый» источник: у номера и дат есть контрольные цифры. Если они сошлись, а значение
совпало с полем визуальной зоны, поле подтверждено (сигнал mrz_match). Строки МЧЗ берём и из ответа
Qwen, и из текста olmOCR — это два независимых прочтения; побеждает версия со сходящимися цифрами.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.registry.rules import today

_LINE = re.compile(r"^[A-Z0-9<]{28,46}$")

# Транслитерация ФИО в МЧЗ внутреннего паспорта РФ (06-documents.md).
_RU_MRZ = dict(zip("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ", "ABVGDE2JZIQKLMNOPRSTUFHC34WXY9678", strict=True))


def _value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - 55
    return 0  # «<» и всё прочее


def check_digit(data: str) -> str:
    weights = (7, 3, 1)
    return str(sum(_value(c) * weights[i % 3] for i, c in enumerate(data)) % 10)


def _ok(data: str, digit: str) -> bool:
    return check_digit(data) == digit


def clean(line: str) -> str:
    """Строка МЧЗ без пробелов; только безопасные исправления: « » и HTML-экранирование → «<»."""
    s = line.strip().upper().replace("&LT;", "<").replace("\\<", "<").replace("«", "<").replace("»", "<")
    return re.sub(r"\s+", "", s)


def find_lines(text: str) -> list[str]:
    """Кандидаты строк МЧЗ в произвольном тексте (ответ модели или markdown olmOCR)."""
    out = []
    for raw in text.splitlines():
        s = clean(raw.strip("|`* "))
        if _LINE.match(s) and "<" in s:
            out.append(s)
    return out


def _date(yymmdd: str, role: str) -> str | None:
    if not re.fullmatch(r"\d{6}", yymmdd):
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    now = today()
    year = 2000 + yy if role == "expiry" or 2000 + yy <= now.year else 1900 + yy
    try:
        return date(year, mm, dd).isoformat()
    except ValueError:
        return None


def _names(part: str) -> tuple[str, str]:
    surname, _, given = part.partition("<<")
    return surname.replace("<", " ").strip(), given.replace("<", " ").strip()


@dataclass
class MrzResult:
    format: str
    fields: dict[str, str | None]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


def parse_td3(l1: str, l2: str) -> MrzResult | None:
    if len(l1) != 44 or len(l2) != 44:
        return None
    surname, given = _names(l1[5:])
    f = {
        "number": l2[0:9].replace("<", ""),
        "nationality": l2[10:13].replace("<", ""),
        "birth_date": _date(l2[13:19], "birth"),
        "sex": l2[20] if l2[20] in "MF" else None,
        "expiry_date": _date(l2[21:27], "expiry"),
        "personal_number": l2[28:42].replace("<", "") or None,
        "surname": surname,
        "given_names": given,
    }
    checks = {
        "number": _ok(l2[0:9], l2[9]),
        "birth_date": _ok(l2[13:19], l2[19]),
        "expiry_date": _ok(l2[21:27], l2[27]),
        "composite": _ok(l2[0:10] + l2[13:20] + l2[21:43], l2[43]),
    }
    if l2[42] != "<" or l2[28:42].strip("<"):
        checks["personal_number"] = _ok(l2[28:42], l2[42])
    return MrzResult("td3", f, checks)


def parse_td1(l1: str, l2: str, l3: str) -> MrzResult | None:
    if len(l1) != 30 or len(l2) != 30 or len(l3) != 30:
        return None
    surname, given = _names(l3)
    f = {
        "number": l1[5:14].replace("<", ""),
        "birth_date": _date(l2[0:6], "birth"),
        "sex": l2[7] if l2[7] in "MF" else None,
        "expiry_date": _date(l2[8:14], "expiry"),
        "nationality": l2[15:18].replace("<", ""),
        "surname": surname,
        "given_names": given,
    }
    checks = {
        "number": _ok(l1[5:14], l1[14]),
        "birth_date": _ok(l2[0:6], l2[6]),
        "expiry_date": _ok(l2[8:14], l2[14]),
        "composite": _ok(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29], l2[29]),
    }
    return MrzResult("td1", f, checks)


def parse_ru_internal(l1: str, l2: str) -> MrzResult | None:
    """МЧЗ внутреннего паспорта РФ: серия и номер, дата рождения, дата выдачи и код подразделения."""
    if len(l1) != 44 or len(l2) != 44 or not l1.startswith("PNRUS"):
        return None
    surname, given = _names(l1[5:])
    f = {
        "series": l2[0:3] + l2[28],
        "number": l2[3:9],
        "birth_date": _date(l2[13:19], "birth"),
        "sex": l2[20] if l2[20] in "MF" else None,
        "issue_date": _date(l2[29:35], "issue"),
        "department_code": f"{l2[35:38]}-{l2[38:41]}",
        "surname": surname,
        "given_names": given,
    }
    checks = {
        "number": _ok(l2[0:9], l2[9]),
        "birth_date": _ok(l2[13:19], l2[19]),
        "issue": _ok(l2[28:42], l2[42]),
        "composite": _ok(l2[0:10] + l2[13:20] + l2[21:43], l2[43]),
    }
    return MrzResult("ru_internal", f, checks)


def parse(lines: list[str], formats: list[str]) -> MrzResult | None:
    """Лучший разбор из найденных строк: сначала версия со сходящимися контрольными цифрами."""
    lines = [clean(x) for x in lines if x]
    found: list[MrzResult] = []
    for i in range(len(lines)):
        for fmt in formats:
            r: MrzResult | None = None
            if fmt == "td3" and i + 1 < len(lines):
                r = parse_td3(lines[i], lines[i + 1])
            elif fmt == "ru_internal" and i + 1 < len(lines):
                r = parse_ru_internal(lines[i], lines[i + 1])
            elif fmt == "td1" and i + 2 < len(lines):
                r = parse_td1(lines[i], lines[i + 1], lines[i + 2])
            if r:
                found.append(r)
    if not found:
        return None
    found.sort(key=lambda r: (r.valid, sum(r.checks.values())), reverse=True)
    return found[0]


def latin_fold(s: str) -> str:
    """Латиница без диакритики для сравнения имён с МЧЗ (Ä→A, Ø→O и т. п.), апострофы удаляются."""
    s = s.upper().replace("ß", "SS").replace("Ø", "O").replace("Æ", "AE")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"['’ʻ`‘]", "", s)
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def ru_to_mrz(s: str) -> str:
    """Кириллица → МЧЗ-транслитерация паспорта РФ (Ё→2, Ч→3, Ш→4 ...)."""
    return re.sub(r"[^A-Z0-9]+", " ", "".join(_RU_MRZ.get(c, c) for c in s.upper())).strip()
