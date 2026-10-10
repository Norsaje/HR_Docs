"""Пригодность снимка до вызова модели: резкость, пересвет, темнота (05-pipeline.md §2.2).

На мутном фото модель скорее выдумает, чем прочитает, поэтому непригодный файл получает
«нечитаемо» с причиной и советом БЕЗ вызова модели. Пороги консервативные и берутся из .env
(QUALITY_*): сомнительное идёт к модели и красит поля в жёлтый, а не отбрасывается.
Резкость — дисперсия лапласиана на копии 1000 px в оттенках серого (Pillow, без OpenCV).
Пересвет (доля пикселей > 250) только сигнал, не отказ: у чистых белых сканов он 0,7–0,9,
и отказ по нему отбраковал бы хорошие документы (замер на открытом наборе).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Quality:
    sharpness: float
    glare: float
    brightness: float
    reject_reason: str | None  # текст для человека; None — снимок годен
    blurry_warn: bool


def sharpness(gray: Image.Image) -> float:
    """Дисперсия лапласиана: чем больше, тем больше чётких границ."""
    w, h = gray.size
    if w < 4 or h < 4:
        return 0.0
    # Край в 1 px Pillow не фильтрует (копирует исходные пиксели) — отрезаем, иначе он искажает дисперсию.
    return float(ImageStat.Stat(gray.filter(_LAPLACIAN).crop((1, 1, w - 1, h - 1))).var[0])


def assess(png: bytes) -> Quality:
    with Image.open(io.BytesIO(png)) as im:
        gray = im.convert("L")
    gray.thumbnail((1000, 1000))
    stat = ImageStat.Stat(gray)
    brightness = float(stat.mean[0])
    hist = gray.histogram()
    glare = sum(hist[251:]) / max(1, sum(hist))
    sharp = sharpness(gray)
    reason = None
    if brightness < _env("QUALITY_DARK_REJECT", 35):
        reason = "Фото слишком тёмное. Сфотографируйте документ при хорошем освещении."
    elif sharp < _env("QUALITY_BLUR_REJECT", 25):
        reason = (
            "Фото размыто. Держите телефон неподвижно и дождитесь фокусировки, документ — целиком в кадре."
        )
    return Quality(sharp, glare, brightness, reason, sharp < _env("QUALITY_BLUR_WARN", 60))


def region_sharpness(png: bytes, bbox: list[int] | None) -> float | None:
    """Резкость области поля по рамке 0–1000 (сигнал region_sharp / region_blurry)."""
    if not bbox or len(bbox) != 4:
        return None
    with Image.open(io.BytesIO(png)) as im:
        page = im.convert("L")
    page.thumbnail((1000, 1000))  # та же шкала, что у порога QUALITY_BLUR_WARN для всей страницы
    w, h = page.size
    x1, y1, x2, y2 = (max(0, min(1000, v)) for v in bbox)
    if x2 - x1 < 5 or y2 - y1 < 5:
        return None
    return sharpness(page.crop((x1 * w // 1000, y1 * h // 1000, x2 * w // 1000, y2 * h // 1000)))
