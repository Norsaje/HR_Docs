"""Парсер МЧЗ на тестовых векторах ICAO 9303 и формате паспорта РФ (06-documents.md)."""

from app.registry.mrz import check_digit, find_lines, latin_fold, parse, parse_td1, parse_td3, ru_to_mrz

TD3 = ("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", "L898902C36UTO7408122F1204159ZE184226B<<<<<10")
TD1 = ("I<UTOD231458907<<<<<<<<<<<<<<<", "7408122F1204159UTO<<<<<<<<<<<6", "ERIKSSON<<ANNA<MARIA<<<<<<<<<<")


def test_check_digit() -> None:
    assert check_digit("L898902C3") == "6"
    assert check_digit("740812") == "2"


def test_td3_vector() -> None:
    r = parse_td3(*TD3)
    assert r is not None and r.valid
    assert r.fields["number"] == "L898902C3"
    assert r.fields["birth_date"] == "1974-08-12"
    assert r.fields["surname"] == "ERIKSSON" and r.fields["given_names"] == "ANNA MARIA"


def test_td3_broken_number() -> None:
    r = parse_td3(TD3[0], TD3[1].replace("L898902C3", "L898902C8"))
    assert r is not None and not r.checks["number"] and not r.valid


def test_td1_vector() -> None:
    r = parse_td1(*TD1)
    assert r is not None and r.valid
    assert r.fields["number"] == "D23145890"
    assert r.fields["given_names"] == "ANNA MARIA"


def test_find_lines_in_ocr_markdown() -> None:
    text = "Some header\n\nP&lt;UTOERIKSSON&lt;&lt;ANNA&lt;MARIA" + "&lt;" * 19 + "\n" + TD3[1] + "\n"
    lines = find_lines(text)
    r = parse(lines, ["td3"])
    assert r is not None and r.valid


def test_ru_internal_synthetic() -> None:
    l1 = "PNRUS" + "IVANOV<<IVAN<IVANOVI3".ljust(39, "<")
    l2 = "4501234568RUS8001014M<<<<<<<9091010770001<50"
    r = parse([l1, l2], ["ru_internal"])
    assert r is not None
    assert r.fields["series"] == "4509" and r.fields["number"] == "123456"
    assert r.fields["birth_date"] == "1980-01-01" and r.fields["department_code"] == "770-001"
    assert r.checks["number"] and r.checks["birth_date"]


def test_name_folding() -> None:
    assert latin_fold("Müller-Lüdenscheidt") == "MULLER LUDENSCHEIDT"
    assert latin_fold("O‘rinboyev") == "ORINBOYEV"
    assert ru_to_mrz("Щербаков Чернов") == "WERBAKOV 3ERNOV"
