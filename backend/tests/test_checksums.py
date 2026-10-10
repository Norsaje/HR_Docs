"""Контрольные суммы на тестовых векторах из docs/spec/06-documents.md."""

import pytest
from app.registry.checksums import inn12_ok, one_digit_fixes, snils_ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("500100732259", True),
        ("123456789047", True),
        ("770708389324", True),
        ("500100732250", False),
        ("123456789048", False),
        ("12345678904", False),
        ("12345678904x", False),
    ],
)
def test_inn12(value: str, ok: bool) -> None:
    assert inn12_ok(value) is ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("11223344595", True),
        ("12345678964", True),
        ("08765430300", True),
        ("12345678965", False),
        ("1234567896", False),
    ],
)
def test_snils(value: str, ok: bool) -> None:
    assert snils_ok(value) is ok


def test_one_digit_fix_suggests_original() -> None:
    # 500100732259 с ошибкой 9→8 в последнем знаке: вариант с исходной цифрой должен найтись.
    assert "500100732259" in one_digit_fixes("500100732258", "inn12")
    assert all(inn12_ok(v) for v in one_digit_fixes("500100732258", "inn12"))
