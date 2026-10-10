"""Реестр документов валиден и ошибки в нём сообщаются с номером строки."""

from pathlib import Path

import pytest
from app.config import APP_ROOT
from app.registry.loader import RegistryError, load_registry

REGISTRY = APP_ROOT / "registry" / "documents.yaml"


def test_registry_is_valid() -> None:
    reg = load_registry(REGISTRY)
    assert reg.default_office().slug == "moscow"
    assert "ru_passport" in reg.documents
    # Каждый документ из групп стран описан, у каждого есть поля минимума.
    for group in reg.country_groups.values():
        for doc in group.documents:
            assert reg.documents[doc].min_fields


def test_group_for_country() -> None:
    reg = load_registry(REGISTRY)
    group = reg.group_for_country("RU")
    assert group is not None and "ru_passport" in group.documents
    assert reg.group_for_country("XX") is None


def test_bad_rule_reference_reports_line(tmp_path: Path) -> None:
    text = REGISTRY.read_text(encoding="utf-8").replace(
        "{id: series_fmt, field: series,", "{id: series_fmt, field: seriez,", 1
    )
    bad = tmp_path / "documents.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(RegistryError) as e:
        load_registry(bad)
    assert "seriez" in str(e.value)
    assert "строка" in str(e.value)


def test_yaml_syntax_error_reports_line(tmp_path: Path) -> None:
    bad = tmp_path / "documents.yaml"
    bad.write_text("version: 1\ndocuments: [unclosed\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="строка"):
        load_registry(bad)
