"""Загрузка и проверка реестра документов `registry/documents.yaml`.

Реестр — единый источник истины: страны → документы → поля → правила. Из него строятся
JSON-схема для модели, промпт, валидаторы, карточка оператора и выгрузки.
Файл проверяется Pydantic-моделью при старте api и worker; ошибка останавливает запуск
с указанием пути и строки в YAML, чтобы правку на защите можно было поправить за минуту.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

FieldType = Literal["digits", "date", "name", "latin_name", "code", "text", "enum"]
DateRole = Literal["birth", "issue", "expiry", "entry", "other"]
Severity = Literal["error", "warning", "info"]
RuleCheck = Literal[
    "pattern",
    "length",
    "not_in_future",
    "not_expired",
    "expires_within",
    "after_field",
    "before_field",
    "max_months_after",
    "min_age_at_issue",
    "ru_passport_age_validity",
    "latin_only",
    "cyrillic_only",
    "in_values",
    "checksum_inn12",
    "checksum_snils",
    "iso_alpha3",
]  # закрытый набор: каждой проверке соответствует функция в app/registry/rules.py
MrzFormat = Literal["ru_internal", "td3", "td1"]

# Проверки, которым нужно второе поле (`other`).
_NEEDS_OTHER = {
    "after_field",
    "before_field",
    "max_months_after",
    "min_age_at_issue",
    "ru_passport_age_validity",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldDef(_Strict):
    label: str
    type: FieldType
    length: int | None = None
    min_len: int | None = None
    max_len: int | None = None
    pattern: str | None = None
    date_role: DateRole | None = None
    values: dict[str, str] | None = None
    prompt_hint: str | None = None
    display_format: str | None = None

    @model_validator(mode="after")
    def _check(self) -> FieldDef:
        if self.type == "enum" and not self.values:
            raise ValueError("у поля типа enum должен быть список values")
        if self.type == "date" and self.date_role is None:
            raise ValueError("у поля типа date должен быть date_role")
        return self


class RuleDef(_Strict):
    id: str
    field: str
    check: RuleCheck
    severity: Severity
    message: str
    applicant_message: str | None = None
    value: Any = None
    min: int | None = None
    max: int | None = None
    other: str | None = None


class DocumentDef(_Strict):
    title: str
    applicant_hint: str
    required: bool
    max_files: int = Field(ge=1, le=10)
    mrz: list[MrzFormat]
    has_photo: bool
    min_fields: list[str]
    expiry_field: str | None
    fields: dict[str, FieldDef]
    rules: list[RuleDef] = []

    @model_validator(mode="after")
    def _references(self) -> DocumentDef:
        names = set(self.fields)
        for f in self.min_fields:
            if f not in names:
                raise ValueError(f"min_fields: поля «{f}» нет в fields")
        if self.expiry_field and self.expiry_field not in names:
            raise ValueError(f"expiry_field: поля «{self.expiry_field}» нет в fields")
        ids: set[str] = set()
        for r in self.rules:
            if r.id in ids:
                raise ValueError(f"правило «{r.id}» объявлено дважды")
            ids.add(r.id)
            if r.field not in names:
                raise ValueError(f"правило «{r.id}»: поля «{r.field}» нет в fields")
            if r.check in _NEEDS_OTHER and not r.other:
                raise ValueError(f"правило «{r.id}»: проверке {r.check} нужно поле other")
            if r.other and r.other not in names:
                raise ValueError(f"правило «{r.id}»: поля «{r.other}» (other) нет в fields")
        return self


class OfficeDef(_Strict):
    slug: str
    name: str
    default: bool = False


class CountryGroup(_Strict):
    title: str
    countries: list[str]
    documents: list[str]


class CrossCheckDef(_Strict):
    id: str
    kind: Literal["names", "equal", "equal_ref", "entered_name"]
    severity: Severity
    message: str
    field: str | None = None
    ref: dict[str, str] | None = None
    documents: list[str] | Literal["all"] = "all"


class Registry(_Strict):
    version: int
    timezone: str
    expiry_warning_days: int
    offices: list[OfficeDef]
    country_groups: dict[str, CountryGroup]
    country_names: dict[str, str]
    eu_alpha3: list[str]
    documents: dict[str, DocumentDef]
    crosschecks: list[CrossCheckDef] = []

    @model_validator(mode="after")
    def _references(self) -> Registry:
        if sum(o.default for o in self.offices) != 1:
            raise ValueError("offices: ровно один офис должен быть default: true")
        seen: dict[str, str] = {}
        for gid, group in self.country_groups.items():
            for doc in group.documents:
                if doc not in self.documents:
                    raise ValueError(f"country_groups.{gid}: документа «{doc}» нет в documents")
            for c in group.countries:
                if c not in self.country_names:
                    raise ValueError(f"country_groups.{gid}: у страны {c} нет названия в country_names")
                if c in seen:
                    raise ValueError(f"страна {c} входит в две группы: {seen[c]} и {gid}")
                seen[c] = gid
        for cc in self.crosschecks:
            if isinstance(cc.documents, list):
                for doc in cc.documents:
                    if doc not in self.documents:
                        raise ValueError(f"crosschecks.{cc.id}: документа «{doc}» нет в documents")
        return self

    # Удобные запросы для API и пайплайна.
    def group_for_country(self, country: str) -> CountryGroup | None:
        for group in self.country_groups.values():
            if country in group.countries:
                return group
        return None

    def default_office(self) -> OfficeDef:
        return next(o for o in self.offices if o.default)


class RegistryError(Exception):
    """Реестр не загрузился: текст уже по-русски и с номером строки."""


def _line_of(root: yaml.Node | None, loc: tuple[int | str, ...]) -> int | None:
    """Найти номер строки YAML по пути ошибки Pydantic (documents → ru_passport → fields → ...)."""
    node = root
    line = node.start_mark.line + 1 if node else None
    for key in loc:
        if isinstance(node, yaml.MappingNode):
            node = next((v for k, v in node.value if k.value == str(key)), None)
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int) and key < len(node.value):
            node = node.value[key]
        else:
            break
        if node is None:
            break
        line = node.start_mark.line + 1
    return line


def load_registry(path: Path) -> Registry:
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.MarkedYAMLError as e:
        line = e.problem_mark.line + 1 if e.problem_mark else "?"
        raise RegistryError(f"{path.name}, строка {line}: синтаксическая ошибка YAML — {e.problem}") from None
    try:
        return Registry.model_validate(raw)
    except ValidationError as e:
        root = yaml.compose(text)
        problems = []
        for err in e.errors():
            # Pydantic добавляет в путь служебные части вроде «function-after[...]» — их пропускаем.
            loc = tuple(p for p in err["loc"] if not (isinstance(p, str) and "[" in p))
            where = ".".join(str(p) for p in loc) or "(корень)"
            problems.append(f"  строка {_line_of(root, loc)}: {where}: {err['msg']}")
        raise RegistryError(f"{path.name}: реестр не прошёл проверку\n" + "\n".join(problems)) from None


@lru_cache
def get_registry() -> Registry:
    """Реестр процесса. Загружается один раз при старте api и worker."""
    from app.config import get_settings  # локальный импорт: тестам реестра настройки не нужны

    return load_registry(get_settings().REGISTRY_PATH)
