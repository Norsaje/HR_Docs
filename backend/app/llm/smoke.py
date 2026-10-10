"""Проверка текущего провайдера модели (`make llm-smoke`): один синтетический документ через шлюз.

Рисует Pillow-ом простую карточку с вымышленными данными (не из открытого набора), отправляет
её в gateway.extract по схеме СНИЛС и печатает провайдера, адрес, модель и время. Запускается
внутри контейнера worker, поэтому проверяет ровно тот LLM_BASE_URL, с которым работает воркер.
"""

from __future__ import annotations

import asyncio
import io

from PIL import Image, ImageDraw

from app.config import get_settings
from app.llm.gateway import Gateway
from app.registry.loader import get_registry


def synthetic_card() -> bytes:
    im = Image.new("RGB", (1200, 760), (205, 235, 210))
    d = ImageDraw.Draw(im)
    lines = [
        "СТРАХОВОЕ СВИДЕТЕЛЬСТВО",
        "ОБЯЗАТЕЛЬНОГО ПЕНСИОННОГО СТРАХОВАНИЯ",
        "112-233-445 95",
        "Ф.И.О. ОБРАЗЦОВ",
        "ИВАН ПЕТРОВИЧ",
    ]
    for i, text in enumerate(lines):
        d.text((60, 80 + i * 110), text, fill=(20, 20, 20), font_size=48)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


async def main() -> None:
    gw = Gateway(get_settings(), get_registry())
    try:
        r = await gw.extract(synthetic_card(), "snils", None)
        number = r.data["fields"]["number"]
        print(
            f"OK: {r.provider} @ {r.base_url_host}, модель {r.model}, {r.duration_ms / 1000:.1f} с, "
            f"режим {r.json_mode}; номер: status={number['status']}, значение={number['raw']}"
        )
    finally:
        await gw.aclose()


if __name__ == "__main__":
    asyncio.run(main())
