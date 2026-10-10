"""Синтетический образец «паспорта РФ» для e2e-скриншотов: вымышленные данные, нарисованы Pillow.

Не копия реального документа и не из открытого набора — только чтобы на скриншотах в репозитории
не было персональных данных. Запуск: python web/operator/e2e/make_fixture.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "fixtures" / "synthetic-passport.png"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size)


im = Image.new("RGB", (1400, 1000), (236, 228, 240))
d = ImageDraw.Draw(im)
d.rectangle([40, 40, 1360, 960], outline=(120, 90, 130), width=4)
d.text((420, 70), "РОССИЙСКАЯ ФЕДЕРАЦИЯ — ОБРАЗЕЦ", fill=(80, 40, 90), font=font(40))
d.rectangle([90, 180, 390, 560], fill=(200, 200, 205), outline=(90, 90, 90), width=2)
d.text((150, 360), "ФОТО", fill=(90, 90, 90), font=font(44))
rows = [
    ("Фамилия", "ОБРАЗЦОВА"),
    ("Имя", "МАРИЯ"),
    ("Отчество", "ТЕСТОВНА"),
    ("Пол", "ЖЕН."),
    ("Дата рождения", "14.03.1990"),
    ("Место рождения", "ГОР. ПРИМЕРНОГОРСК"),
    ("Дата выдачи", "20.04.2010"),
    ("Код подразделения", "770-001"),
]
y = 190
for label, value in rows:
    d.text((450, y), label, fill=(110, 110, 120), font=font(26))
    d.text((760, y - 4), value, fill=(20, 20, 25), font=font(34))
    y += 72
d.text((1250, 200), "45 09", fill=(190, 30, 30), font=font(40))
d.text((1250, 260), "123456", fill=(190, 30, 30), font=font(40))
d.text((90, 860), "ОБРАЗЕЦ ДЛЯ ТЕСТОВ · ВЫМЫШЛЕННЫЕ ДАННЫЕ", fill=(150, 60, 60), font=font(30))
OUT.parent.mkdir(parents=True, exist_ok=True)
im.save(OUT)
print(OUT)
