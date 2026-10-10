"""Цвет поля — прозрачное правило из сигналов (docs/spec/05-pipeline.md §2.7). Цель — ноль ложных зелёных.

Самооценке модели не доверяем. Зелёный — только если значение подтвердил независимый «твёрдый»
источник (контрольная сумма, МЧЗ с верными контрольными цифрами, текстовый слой PDF) или второй
независимый прочитавший (olmOCR) дал то же самое на резкой области печатного поля.
Все сигналы сохраняются в fields.signals, а SIGNALS_RU объясняет оператору, почему поле такого цвета.
"""

from __future__ import annotations

SIGNALS_RU: dict[str, str] = {
    "checksum_ok": "подтверждено контрольной суммой",
    "checksum_fail": "контрольная сумма не сходится — номер прочитан с ошибкой или неверен",
    "mrz_match": "совпадает с машиночитаемой зоной (контрольные цифры верны)",
    "mrz_mismatch": "расходится с машиночитаемой зоной — проверьте",
    "text_layer_match": "совпадает с текстовым слоем PDF",
    "ocr_agree": "второй распознаватель (olmOCR) прочитал то же значение",
    "ocr_disagree": "второй распознаватель (olmOCR) прочитал иначе — проверьте",
    "ocr_silent": "второй распознаватель не нашёл это значение — подтвердить нечем",
    "ocr_unavailable": "второй распознаватель был недоступен — подтвердить нечем",
    "ocr_no_vote": "для кириллицы голос второго распознавателя не учитывается",
    "region_sharp": "область поля резкая",
    "region_blurry": "область поля размыта — стоит взглянуть",
    "handwritten": "вписано от руки — стоит взглянуть",
    "stamp": "в штампе или печати — стоит взглянуть",
    "rule_fail": "нарушено правило проверки — нужен человек",
    "rule_warning": "есть предупреждение правила — стоит взглянуть",
    "unreadable": "модель не смогла прочитать значение однозначно",
    "absent": "поля нет на этом документе или странице",
    "single_source": "прочитал только один источник — нужно проверить",
    "operator_edit": "исправлено оператором",
    "hint": "есть подсказка: вероятная замена одного символа",
}

HARD = {"checksum_ok", "mrz_match", "text_layer_match"}


def decide(status: str, kind: str, signals: set[str], required: bool) -> str:
    """green | yellow | red | grey по набору сигналов поля."""
    if status == "absent":
        return "red" if required else "grey"
    if status == "unreadable":
        return "red" if required else "yellow"
    if signals & {"rule_fail", "checksum_fail", "mrz_mismatch", "ocr_disagree"}:
        # ocr_disagree без арбитра — «нет большинства»: значение A, цвет красный, варианты в candidates.
        return "red"
    printed = kind == "printed"
    if signals & HARD and printed and "rule_warning" not in signals:
        return "green"
    if "ocr_agree" in signals and "region_sharp" in signals and printed and "rule_warning" not in signals:
        return "green"
    return "yellow"
