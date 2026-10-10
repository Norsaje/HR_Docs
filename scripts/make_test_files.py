"""Тестовые файлы для ручной проверки (`make test-files` → data/test/, папка в .gitignore).

По 1–2 файла каждого вида из открытого набора с понятными именами (01_ru_passport.jpg …),
fake.jpg (текст под видом картинки), big.jpg (больше лимита), blurry.jpg и dark.jpg (испорченные
копии паспорта РФ), expired_*.* — документы, у которых модель уже прочитала истёкший срок
(по данным стенда; если стенд их ещё не разбирал — файлов не будет, скрипт так и скажет).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.dataset import load_samples  # noqa: E402

OUT = ROOT / "data" / "test"
KINDS = ["ru_passport", "snils", "inn", "ru_driver_license", "work_patent", "kg_driver_license",
         "by_passport", "in_passport"]


# data/test_1 — ровно те файлы, что используются в README («Полный проход»), по папке на заявку.
P1, P2 = "Первый пакет", "Второй пакет"
WALK = {
    "01_Россия_Алексеева": [(P1, "Паспорт РФ Алексеева Анастасия Игоревна.jpg", "паспорт.jpg"),
                           (P1, "СНИЛС Алексеева Анастасия Игоревна.jpg", "снилс.jpg"),
                           (P1, "ИНН Алексеева Анастасия Игоревна.png", "инн.png")],
    "02_Россия_Киселев": [(P1, "Паспорт РФ Киселев Максим Васильевич.jpg", "паспорт.jpg"),
                         (P1, "СНИЛС Киселев Максим Васильевич.jpg", "снилс.jpg"),
                         (P1, "ИНН Киселев Максим Васильевич.jpeg", "инн_дозагрузить_по_ссылке.jpeg")],
    "03_Россия_Морозов": [(P2, "Паспорт РФ Морозов Виталий Михайлович.jpg", "паспорт_1.jpg"),
                         (P2, "Паспорт РФ Морозов Виталий Михайлович (повторное фото).jpg", "паспорт_2.jpg"),
                         (P1, "Размытое фото документа.jpg", "снилс_размытое.jpg"),
                         (P2, "Тёмное фото документа.jpg", "инн_тёмное.jpg"),
                         (P2, "ВУ РФ (лицо) Сидоров Максим Артемович.jpg", "ву_рф.jpg")],
    "04_Узбекистан_Hakimov": [(P1, "Патент (лицо) Hakimov Timur.jpg", "патент_лицо.jpg"),
                              (P1, "Патент (оборот) Hakimov Timur.png", "патент_оборот.png")],
    "05_Беларусь_Sidarenka": [(P1, "Паспорт Беларуси Sidarenka Andrei.jpeg", "паспорт_истёк.jpeg")],
    "06_Киргизия_Mamytov": [(P1, "ВУ Киргизии Mamytov Bakyt.pdf", "ву_киргизии.pdf")],
    "07_Индия_Patel": [(P1, "Паспорт Индии Anil Patel.jpg", "паспорт.jpg")],
    "08_Россия_Соколов": [(P2, "Паспорт РФ - страница прописки (не тот документ).jpg", "паспорт_не_та_страница.jpg"),
                         (P1, "Чек (не документ).jpeg", "инн_чек.jpeg"),
                         (P1, "ВУ РФ (лицо) Соколов Артем Геннадьевич.jpeg", "снилс_а_на_самом_деле_ву.jpeg")],
}


def make_walkthrough_set() -> None:
    out = ROOT / "data" / "test_1"
    for folder, files in WALK.items():
        (out / folder).mkdir(parents=True, exist_ok=True)
        for pack, src, dst in files:
            shutil.copyfile(ROOT / "data" / "open" / pack / src, out / folder / dst)
    for name in ("fake.jpg", "big.jpg"):  # отказы: не картинка и больше 20 МБ
        shutil.copyfile(OUT / name, out / name)
    print(f"data/test_1: {len(WALK)} папок-заявок для README + fake.jpg, big.jpg")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    samples = load_samples()
    by_kind: dict[str, list] = defaultdict(list)
    for s in samples:
        by_kind[s.kind].append(s)
    n = 0
    for kind in KINDS:
        for i, s in enumerate(by_kind[kind][:2], start=1):
            n += 1
            side = f"_{s.side}" if kind in ("ru_driver_license", "work_patent") else ""
            shutil.copyfile(s.path, OUT / f"{n:02d}_{kind}{side}_{i}{s.path.suffix.lower()}")
    # Негативы из набора: не тот разворот и чек — для проверки «не тот документ».
    for kind, name in (("ru_passport_registration", "wrong_page"), ("not_document", "receipt")):
        if by_kind[kind]:
            s = by_kind[kind][0]
            shutil.copyfile(s.path, OUT / f"{name}{s.path.suffix.lower()}")

    (OUT / "fake.jpg").write_text("Это текстовый файл, а не фотография.\n" * 50, encoding="utf-8")
    with open(OUT / "big.jpg", "wb") as f:  # настоящая сигнатура JPEG + 21 МБ — отказ по размеру
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * (21 * 1024 * 1024))
    base = by_kind["ru_passport"][0].path
    im = ImageOps.exif_transpose(Image.open(base)).convert("RGB")
    im.filter(ImageFilter.GaussianBlur(14)).save(OUT / "blurry.jpg", quality=85)
    ImageEnhance.Brightness(im).enhance(0.08).save(OUT / "dark.jpg", quality=85)

    # Истёкшие документы — по тому, что модель прочитала на стенде (expires_on < сегодня).
    q = ("SELECT DISTINCT f.sha256, d.doc_type FROM documents d JOIN files f ON f.document_id=d.id "
         "WHERE d.expires_on < current_date")
    res = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "hr", "-At",
                          "-F", "|", "-c", q], cwd=ROOT, capture_output=True, text=True)
    sha_to = {s.sha256: s for s in samples}
    expired = 0
    for line in res.stdout.splitlines():
        sha, doc_type = line.split("|")
        if sha in sha_to:
            expired += 1
            src = sha_to[sha].path
            shutil.copyfile(src, OUT / f"expired_{doc_type}_{expired}{src.suffix.lower()}")
    print(f"data/test: {n} файлов документов, wrong_page, receipt, fake.jpg, big.jpg, blurry.jpg, dark.jpg; "
          f"истёкших: {expired}" + ("" if expired else " (стенд ещё не находил истёкших документов в наборе)"))
    make_walkthrough_set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
