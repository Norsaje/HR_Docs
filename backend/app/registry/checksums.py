"""Контрольные суммы ИНН-12 и СНИЛС и подсказки по одной ошибочной цифре.

Контрольная сумма — «твёрдый» независимый источник: если она сошлась, номер прочитан
верно с высокой вероятностью, и поле может стать зелёным без второго OCR.
При несовпадении перебираем замены одной цифры на визуально похожую и предлагаем
оператору варианты, при которых сумма сходится. Значение автоматически не меняем.
"""

from __future__ import annotations

_W1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_W2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)

# Цифры, которые OCR и модели путают на плохих фото (docs/spec/05-pipeline.md, §2.5).
LOOKALIKE: dict[str, str] = {
    "0": "8693",
    "8": "06935",
    "6": "0895",
    "9": "0863",
    "3": "0859",
    "5": "0863",
    "1": "74",
    "7": "142",
    "4": "17",
    "2": "7",
}


def inn12_ok(value: str) -> bool:
    if len(value) != 12 or not value.isdigit():
        return False
    d = [int(c) for c in value]
    n11 = sum(a * b for a, b in zip(d[:10], _W1, strict=True)) % 11 % 10
    n12 = sum(a * b for a, b in zip(d[:11], _W2, strict=True)) % 11 % 10
    return d[10] == n11 and d[11] == n12


def snils_ok(value: str) -> bool:
    """11 цифр; контрольное число проверяется для номеров больше 001-001-998."""
    if len(value) != 11 or not value.isdigit():
        return False
    body, control = value[:9], int(value[9:])
    if int(body) <= 1001998:
        return True
    total = sum(int(c) * w for c, w in zip(body, range(9, 0, -1), strict=True))
    if total < 100:
        expected = total
    elif total in (100, 101):
        expected = 0
    else:
        expected = total % 101
        if expected == 100:
            expected = 0
    return control == expected


def one_digit_fixes(value: str, check: str) -> list[str]:
    """Варианты с заменой одной цифры на похожую, при которых сумма сходится (подсказка, не исправление)."""
    validate = {"inn12": inn12_ok, "snils": snils_ok}[check]
    found = []
    for i, ch in enumerate(value):
        for alt in LOOKALIKE.get(ch, ""):
            candidate = value[:i] + alt + value[i + 1 :]
            if validate(candidate):
                found.append(candidate)
    return found
