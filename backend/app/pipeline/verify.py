"""Сверка прочтения модели с независимыми источниками → сигналы, цвет, подсказки (stage=verify).

Чистая функция без БД и сети, поэтому её легко проверить тестами. Для каждого поля:
правила реестра (rule_fail / rule_warning), контрольные суммы ИНН/СНИЛС (+ подсказки по одной
цифре), МЧЗ с верными контрольными цифрами (mrz_match / mrz_mismatch), текстовый слой PDF,
голос olmOCR, резкость области поля, тип записи. Цвет решает confidence.decide.
Значение никогда не меняется автоматически: варианты и подсказки идут оператору.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.llm.schema_builder import Extraction
from app.pipeline.confidence import decide
from app.pipeline.ocr_vote import OcrCandidates, vote
from app.pipeline.quality import region_sharpness
from app.registry.checksums import inn12_ok, one_digit_fixes, snils_ok
from app.registry.loader import DocumentDef, FieldDef, Registry
from app.registry.mrz import MrzResult, latin_fold, ru_to_mrz
from app.registry.rules import evaluate

_CHECKSUM = {"checksum_inn12": ("inn12", inn12_ok), "checksum_snils": ("snils", snils_ok)}


@dataclass
class Verdict:
    value: str | None
    color: str
    signals: list[str]
    candidates: dict[str, Any] = field(default_factory=dict)
    hint: str | None = None


def inn10_ok(v: str) -> bool:
    """ИНН организации (10 цифр): подсказка, что загружен ИНН юрлица, а не физлица."""
    if len(v) != 10 or not v.isdigit():
        return False
    w = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    return int(v[9]) == sum(int(a) * b for a, b in zip(v[:9], w, strict=True)) % 11 % 10


def _mrz_value(key: str, mrz: MrzResult) -> tuple[str | None, bool]:
    """Значение поля из МЧЗ и можно ли ему доверять (сошлись нужные контрольные цифры)."""
    c, f = mrz.checks, mrz.fields
    if key in ("number", "birth_date", "expiry_date"):
        return f.get(key), c.get(key, False)
    if key in ("issue_date", "department_code") and mrz.format == "ru_internal":
        return f.get(key), c.get("issue", False)
    if key == "series" and mrz.format == "ru_internal":
        return f.get("series"), c.get("issue", False) and c.get("number", False)
    if key in ("sex", "nationality", "personal_number"):
        return f.get(key), mrz.valid
    if key == "surname":
        return f.get("surname"), mrz.valid
    if key in ("given_name", "given_names"):
        return f.get("given_names"), mrz.valid
    return None, False


def _mrz_signal(key: str, fdef: FieldDef, value: str, mrz: MrzResult) -> tuple[str | None, str | None]:
    """(сигнал, значение МЧЗ) — mrz_match / mrz_mismatch или (None, None), если сравнивать нечего."""
    mv, trusted = _mrz_value(key, mrz)
    if not mv or not trusted:
        return None, None
    if fdef.type in ("name", "latin_name"):
        mine = ru_to_mrz(value) if re.search(r"[А-Яа-я]", value) else latin_fold(value)
        theirs = latin_fold(mv)
        if key in ("given_name",) and mrz.format == "ru_internal":
            theirs = theirs.split(" ")[0] if theirs else theirs  # в МЧЗ РФ имя и отчество вместе
        # Для имён расхождение не объявляем: варианты транслитерации и обрезка в МЧЗ законны.
        return ("mrz_match", None) if mine and mine == theirs else (None, None)
    norm = re.sub(r"[\s\-]", "", mv.upper()) if fdef.type != "date" else mv
    mine = re.sub(r"[\s\-]", "", value.upper()) if fdef.type != "date" else value
    if fdef.type == "digits":
        norm, mine = re.sub(r"\D", "", norm), re.sub(r"\D", "", mine)
    return ("mrz_match", None) if norm == mine else ("mrz_mismatch", mv)


def _in_text_layer(fdef: FieldDef, value: str, text: str) -> bool:
    compact = "".join(text.split()).upper().replace("Ё", "Е")
    if fdef.type == "date":
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return False
        return d.strftime("%d.%m.%Y") in compact
    probe = "".join(value.split()).upper()
    if fdef.type == "digits":
        return len(probe) >= 4 and probe in re.sub(r"\D", "", compact)
    return len(probe) >= 3 and probe in compact


def verify_fields(
    doc: DocumentDef,
    registry: Registry,
    ext: Extraction,
    values: dict[str, str | None],
    *,
    ocr: OcrCandidates | None,
    ocr_status: str,  # ok | unavailable | off
    mrz: MrzResult | None,
    text_layer: str | None,
    page_png: bytes | None,
    warn_days: int,
    votes_cyrillic: bool = True,
) -> dict[str, Verdict]:
    rule_hits: dict[str, list[str]] = {}
    for rule, _msg in evaluate(doc, values, registry, warn_days):
        if rule.severity in ("error", "warning"):
            rule_hits.setdefault(rule.field, []).append(
                "rule_fail" if rule.severity == "error" else "rule_warning"
            )
    blur_warn = float(os.environ.get("QUALITY_BLUR_WARN", 60))
    out: dict[str, Verdict] = {}
    for key, fdef in doc.fields.items():
        fv = ext.fields.get(key)
        if fv is None:
            continue
        value = values.get(key)
        required = key in doc.min_fields
        if fv.status != "ok" or value is None:
            status = fv.status if fv.status != "ok" else "unreadable"
            out[key] = Verdict(None, decide(status, fv.kind, {status}, required), [status])
            continue
        signals: list[str] = list(dict.fromkeys(rule_hits.get(key, [])))
        cands: dict[str, Any] = {}
        hint = None
        for rule in doc.rules:
            if rule.field == key and rule.check in _CHECKSUM:
                kind, check = _CHECKSUM[rule.check]
                if check(value):
                    signals.append("checksum_ok")
                else:
                    signals.append("checksum_fail")
                    fixes = (
                        one_digit_fixes(value, kind) if len(value) == (12 if kind == "inn12" else 11) else []
                    )
                    if 0 < len(fixes) <= 2:
                        cands["hints"] = fixes
                        hint = "Возможно, одна цифра прочитана неверно; сумма сходится для: " + ", ".join(
                            fixes
                        )
                    elif kind == "inn12" and inn10_ok(value):
                        hint = "10 цифр с верной контрольной суммой — это ИНН организации, а нужен ИНН физлица (12 цифр)"
        if mrz is not None:
            sig, mv = _mrz_signal(key, fdef, value, mrz)
            if sig:
                signals.append(sig)
            if mv:
                cands["mrz"] = mv
                hint = hint or f"В машиночитаемой зоне: {mv}"
        if text_layer and _in_text_layer(fdef, value, text_layer):
            signals.append("text_layer_match")
        if ocr_status == "ok" and ocr is not None:
            sig2, near = vote(fdef, value, ocr, votes_cyrillic)
            # Расхождение по цифрам объявляем только для длинных номеров: короткие (серия, год)
            # легко спутать с другим числом того же документа.
            if sig2 == "ocr_disagree" and not (fdef.type == "digits" and len(value) >= 6):
                sig2 = "ocr_silent"
            signals.append(sig2)
            if near and sig2 == "ocr_disagree":
                cands["ocr"] = near
        elif ocr_status == "unavailable":
            signals.append("ocr_unavailable")
        if page_png is not None:
            rs = region_sharpness(page_png, fv.bbox)
            if rs is not None:
                signals.append("region_sharp" if rs >= blur_warn else "region_blurry")
        if fv.kind in ("handwritten", "stamp"):
            signals.append(fv.kind)
        if not set(signals) & {"checksum_ok", "mrz_match", "text_layer_match", "ocr_agree"}:
            signals.append("single_source")
        if hint:
            signals.append("hint")
        out[key] = Verdict(value, decide("ok", fv.kind, set(signals), required), signals, cands, hint)
    return out
