"""Цвет поля по сигналам (05-pipeline §2.7): зелёный только при независимом подтверждении.

Проверяем на синтетических ответах модели (без сети): контрольная сумма, МЧЗ, голос OCR,
расхождение OCR, нарушение правила, нечитаемое обязательное поле, подсказки по одной цифре,
и что тёмный снимок отбраковывается без модели.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from app.config import APP_ROOT
from app.llm.schema_builder import Extraction
from app.pipeline.confidence import SIGNALS_RU, decide
from app.pipeline.ocr_vote import candidates
from app.pipeline.quality import assess
from app.pipeline.verify import verify_fields
from app.registry.loader import Registry, load_registry
from app.registry.mrz import parse
from app.registry.rules import normalize
from PIL import Image, ImageDraw


@pytest.fixture(scope="module")
def reg() -> Registry:
    return load_registry(APP_ROOT / "registry" / "documents.yaml")


def ext(reg: Registry, doc_type: str, raw: dict[str, str | None], mrz: list[str] | None = None) -> Extraction:
    fields: dict[str, Any] = {}
    for key in reg.documents[doc_type].fields:
        v = raw.get(key)
        fields[key] = {"raw": v, "status": "ok" if v else "absent", "kind": "printed", "bbox": None}
    return Extraction.model_validate(
        {
            "doc_type": doc_type,
            "page_side": "front",
            "orientation": 0,
            "is_document": True,
            "mrz_lines": mrz or [],
            "fields": fields,
        }
    )


def run(
    reg: Registry,
    e: Extraction,
    ocr_text: str | None = None,
    ocr_status: str = "off",
    mrz_formats: list[str] | None = None,
) -> dict[str, Any]:
    doc = reg.documents[e.doc_type]
    values = {k: normalize(doc.fields[k], v.raw if v.status == "ok" else None) for k, v in e.fields.items()}
    mrz = parse(e.mrz_lines, mrz_formats) if mrz_formats else None
    return verify_fields(
        doc,
        reg,
        e,
        values,
        ocr=candidates(ocr_text) if ocr_text else None,
        ocr_status=ocr_status,
        mrz=mrz,
        text_layer=None,
        page_png=None,
        warn_days=30,
    )


def test_checksum_gives_green_and_bad_checksum_red_with_hint(reg: Registry) -> None:
    good = run(reg, ext(reg, "snils", {"number": "112-233-445 95"}))
    assert good["number"].color == "green" and "checksum_ok" in good["number"].signals
    bad = run(reg, ext(reg, "inn", {"number": "500100732258"}))
    assert bad["number"].color == "red"
    assert "500100732259" in bad["number"].candidates.get("hints", [])
    assert bad["number"].value == "500100732258"  # значение само не меняется


def test_inn10_hint(reg: Registry) -> None:
    v = run(reg, ext(reg, "inn", {"number": "7707083893"}))["number"]
    assert v.color == "red" and v.hint and "организации" in v.hint


def test_name_without_confirmation_is_yellow_not_green(reg: Registry) -> None:
    v = run(reg, ext(reg, "snils", {"number": "112-233-445 95", "surname": "ИВАНОВ"}))["surname"]
    assert v.color == "yellow" and "single_source" in v.signals


def test_ocr_agree_needs_sharp_region(reg: Registry) -> None:
    # Без страницы (нет region_sharp) согласие OCR не даёт зелёного — только жёлтый.
    v = run(
        reg,
        ext(reg, "ru_passport", {"surname": "ИВАНОВ", "series": "4509", "number": "123456"}),
        ocr_text="ФАМИЛИЯ ИВАНОВ\n45 09 123456",
        ocr_status="ok",
    )
    assert "ocr_agree" in v["surname"].signals and v["surname"].color == "yellow"


def test_ocr_disagree_on_long_number_is_red(reg: Registry) -> None:
    v = run(
        reg,
        ext(reg, "ru_driver_license", {"number": "9912345678", "surname": "ИВАНОВ"}),
        ocr_text="5. 99 12 345 670",
        ocr_status="ok",
    )["number"]
    assert "ocr_disagree" in v.signals and v.color == "red" and v.candidates["ocr"] == "9912345670"


def test_ocr_unavailable_signal(reg: Registry) -> None:
    v = run(
        reg, ext(reg, "snils", {"number": "112-233-445 95", "surname": "ИВАНОВ"}), ocr_status="unavailable"
    )
    assert "ocr_unavailable" in v["surname"].signals and v["surname"].color == "yellow"


def test_mrz_match_and_mismatch(reg: Registry) -> None:
    lines = ["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", "L898902C36UTO7408122F1204159ZE184226B<<<<<10"]
    e = ext(
        reg,
        "eu_id",
        {
            "number": "L898902C3",
            "surname": "ERIKSSON",
            "given_names": "ANNA MARIA",
            "birth_date": "12.08.1974",
            "expiry_date": "15.04.2032",
        },
        mrz=lines,
    )
    v = run(reg, e, mrz_formats=["td3"])
    assert "mrz_match" in v["number"].signals and v["number"].color == "green"
    assert "mrz_match" in v["surname"].signals
    e2 = ext(reg, "eu_id", {"number": "L898902C8", "surname": "ERIKSSON"}, mrz=lines)
    v2 = run(reg, e2, mrz_formats=["td3"])["number"]
    assert v2.color == "red" and v2.candidates["mrz"] == "L898902C3"


def test_rule_fail_and_unreadable_required(reg: Registry) -> None:
    v = run(reg, ext(reg, "ru_passport", {"series": "450", "surname": None}))
    assert v["series"].color == "red" and "rule_fail" in v["series"].signals
    assert v["surname"].color == "red"  # обязательное поле не прочитано
    assert v["patronymic"].color == "grey"  # необязательного нет


def test_decide_and_signals_ru() -> None:
    assert decide("ok", "handwritten", {"checksum_ok"}, True) == "yellow"
    assert decide("ok", "printed", {"checksum_ok", "rule_warning"}, True) == "yellow"
    assert all(SIGNALS_RU[s] for s in ("checksum_ok", "ocr_disagree", "mrz_match", "single_source"))


def _png(color: int, text: bool) -> bytes:
    im = Image.new("RGB", (1200, 800), (color, color, color))
    if text:
        d = ImageDraw.Draw(im)
        for i in range(12):
            d.text((50, 40 + i * 60), "ДОКУМЕНТ 1234567890 ОБРАЗЕЦ", fill=(0, 0, 0), font_size=40)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_quality_gate() -> None:
    assert assess(_png(10, False)).reject_reason is not None  # тёмное
    assert assess(_png(230, False)).reject_reason is not None  # пустое — размыто
    assert assess(_png(230, True)).reject_reason is None  # чёткий текст проходит
