"""JSON-схема ответа модели и описание полей для промпта — из реестра документов.

Схема задаёт только форму ответа: ключи, типы, enum, длины. Никаких `pattern`/`format`:
на смазанном фото шаблон в декодере заставляет модель «дорисовать» цифры, поэтому
шаблоны и контрольные суммы проверяет код после генерации. Схема плоская (без $ref/oneOf),
порядок свойств «сначала прочитать (doc_type, mrz_lines), потом разложить (fields)».
Ответ проверяется той же схемой (jsonschema), затем раскладывается в типизированную модель.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict

from app.registry.loader import DocumentDef, FieldDef, Registry

FIELD_STATUS = ["ok", "unreadable", "absent"]
FIELD_KIND = ["printed", "handwritten", "stamp"]
PAGE_SIDES = ["main_spread", "front", "back", "other"]
ORIENTATIONS = [0, 90, 180, 270]


def field_schema(f: FieldDef) -> dict[str, Any]:
    raw: dict[str, Any] = {"type": ["string", "null"], "maxLength": f.max_len or 64}
    if f.type == "enum" and f.values:
        raw = {"type": ["string", "null"], "enum": [*f.values.keys(), "other", None]}
    return {
        "type": "object",
        "properties": {
            "raw": raw,
            "status": {"type": "string", "enum": FIELD_STATUS},
            "kind": {"type": "string", "enum": FIELD_KIND},
            "bbox": {
                "type": ["array", "null"],
                "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                "minItems": 4,
                "maxItems": 4,
            },
        },
        "required": ["raw", "status", "kind", "bbox"],
        "additionalProperties": False,
    }


def extract_schema(registry: Registry, doc_type: str) -> dict[str, Any]:
    doc = registry.documents[doc_type]
    return {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": [*registry.documents.keys(), "other"]},
            "page_side": {"type": "string", "enum": PAGE_SIDES},
            "orientation": {"type": "integer", "enum": ORIENTATIONS},
            "is_document": {"type": "boolean"},
            "mrz_lines": {"type": "array", "items": {"type": "string", "maxLength": 50}, "maxItems": 3},
            "fields": {
                "type": "object",
                "properties": {name: field_schema(f) for name, f in doc.fields.items()},
                "required": list(doc.fields),
                "additionalProperties": False,
            },
        },
        "required": ["doc_type", "page_side", "orientation", "is_document", "mrz_lines", "fields"],
        "additionalProperties": False,
    }


def schema_hash(schema: dict[str, Any]) -> str:
    """sha256 канонического JSON: пишется в extraction_runs, чтобы видеть, какой схемой получен ответ."""
    canonical = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def schema_errors(schema: dict[str, Any], data: Any) -> list[str]:
    """Отклонения ответа от схемы; пустой список — ответ по схеме.

    Текст ошибки — только путь и нарушенное правило схемы, без самого значения:
    значение может быть персональными данными, а текст ошибки попадает в журнал.
    """
    validator = Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.path]):
        where = "/".join(str(p) for p in err.path) or "(корень)"
        errors.append(f"{where}: нарушено правило схемы «{err.validator}»")
    return errors


def fields_block(doc: DocumentDef) -> str:
    """Описание полей для промпта: одна строка на поле, из реестра (без примеров значений)."""
    lines = []
    for name, f in doc.fields.items():
        line = f"- `{name}` — {f.label}"
        if f.type == "enum" and f.values:
            options = ", ".join(f"{k} ({v})" for k, v in f.values.items())
            line += f"; одно из: {options}, other"
        elif f.type == "date":
            line += "; дата так, как напечатана"
        if f.prompt_hint:
            line += f"; {f.prompt_hint}"
        lines.append(line)
    return "\n".join(lines)


def doc_type_list(registry: Registry) -> str:
    return ", ".join(f"{key} ({d.title})" for key, d in registry.documents.items()) + ", other"


# --- Типизированный ответ: то же, что схема, но удобно для кода пайплайна ---


class FieldValue(BaseModel):
    model_config = ConfigDict(frozen=True)
    raw: str | None
    status: Literal["ok", "unreadable", "absent"]
    kind: Literal["printed", "handwritten", "stamp"]
    bbox: list[int] | None


class Extraction(BaseModel):
    model_config = ConfigDict(frozen=True)
    doc_type: str
    page_side: str
    orientation: int
    is_document: bool
    mrz_lines: list[str]
    fields: dict[str, FieldValue]
