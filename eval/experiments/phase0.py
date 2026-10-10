"""Эксперименты фазы 0 (docs/spec/09-model-gateway.md): olmOCR на кириллице, скорость и
валидность Qwen3.8, поддержка structured outputs для схем всех восьми видов.

Все вызовы идут через app.llm.Gateway — тот же код, что у воркера. Эталонной разметки
ещё нет, поэтому правильность цифр меряем объективно контрольными суммами ИНН/СНИЛС,
а для остального — согласием двух независимых источников (Qwen и olmOCR).
В отчёт попадают только агрегаты и префиксы sha256; значения полей — только в eval/cache/.
Запуск: make experiments
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]


def load_env() -> None:
    """Переменные из .env в окружение процесса (значения не печатаются)."""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = re.split(r"\s+#", m.group(2), maxsplit=1)[0].strip()


load_env()

import pypdfium2 as pdfium  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm.errors import ModelError  # noqa: E402
from app.llm.gateway import Gateway  # noqa: E402
from app.registry.checksums import inn12_ok, snils_ok  # noqa: E402
from app.registry.loader import get_registry  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from eval.dataset import Sample, load_samples  # noqa: E402

CACHE = ROOT / "eval" / "cache" / "phase0"
REPORTS = ROOT / "eval" / "reports"


def page_png(path: Path, max_side: int = 2400) -> bytes:
    """Нормализация как в пайплайне: EXIF-поворот, RGB, длинная сторона до 2400, PNG."""
    if path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(path))
        page = pdf[0]
        scale = 2000 / max(page.get_size())
        im = page.render(scale=scale).to_pil().convert("RGB")
    else:
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def norm_name(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").upper().replace("Ё", "Е")).strip()


def digit_candidates(text: str) -> set[str]:
    """Все последовательности цифр с разделителями-пробелами/дефисами, сведённые к цифрам."""
    return {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d \-]*\d|\d", text)}


def cyr_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters) if letters else 0.0


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


async def timed_extract(gw: Gateway, png: bytes, doc_type: str) -> dict[str, Any]:
    t = time.monotonic()
    try:
        r = await gw.extract(png, doc_type, None)
        return {
            "ok": True,
            "data": r.data,
            "duration_ms": r.duration_ms,
            "inner_retries": r.inner_retries,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "json_mode": r.json_mode,
        }
    except ModelError as e:
        return {
            "ok": False,
            "code": e.code,
            "detail": e.detail[:200],
            "duration_ms": int((time.monotonic() - t) * 1000),
        }


async def timed_ocr(gw: Gateway, png: bytes) -> dict[str, Any]:
    t = time.monotonic()
    try:
        r = await gw.ocr(png)
        return {"ok": True, "markdown": r.markdown, "duration_ms": r.duration_ms}
    except ModelError as e:
        return {
            "ok": False,
            "code": e.code,
            "duration_ms": int((time.monotonic() - t) * 1000),
        }


def raw(data: dict[str, Any], field: str) -> str | None:
    f = data.get("fields", {}).get(field)
    return f["raw"] if f and f["status"] == "ok" else None


# ---------------- эксперимент 1: olmOCR на документах РФ ----------------


async def experiment1(gw: Gateway, samples: list[Sample]) -> tuple[list[str], dict[str, Any]]:
    rf = [s for s in samples if s.kind in ("ru_passport", "snils", "inn")]
    pngs = {s.sha256: page_png(s.path) for s in rf}
    ocr = await asyncio.gather(*(timed_ocr(gw, pngs[s.sha256]) for s in rf))
    qwen = await asyncio.gather(*(timed_extract(gw, pngs[s.sha256], s.kind) for s in rf))

    rows, agg = (
        [],
        {
            "num_ocr_checksum": 0,
            "num_qwen_checksum": 0,
            "num_total": 0,
            "num_agree": 0,
            "num_compared": 0,
            "name_agree": 0,
            "name_total": 0,
            "pass_series_number": 0,
            "pass_total": 0,
        },
    )
    for s, o, q in zip(rf, ocr, qwen, strict=True):
        if not o["ok"] or not q["ok"]:
            rows.append(
                f"| {s.short} | {s.kind} | OCR {o.get('code', 'ok')} / Qwen {q.get('code', 'ok')} | | | |"
            )
            continue
        text, data = o["markdown"], q["data"]
        cands = digit_candidates(text)
        name_hits = []
        for f in ("surname", "given_name", "patronymic"):
            v = norm_name(raw(data, f))
            if v:
                agg["name_total"] += 1
                hit = v in norm_name(text)
                agg["name_agree"] += hit
                name_hits.append("+" if hit else "−")
        num_note = ""
        if s.kind in ("snils", "inn"):
            check, length = (snils_ok, 11) if s.kind == "snils" else (inn12_ok, 12)
            qv = re.sub(r"\D", "", raw(data, "number") or "")
            ocr_ok = any(len(c) == length and check(c) for c in cands)
            q_ok = len(qv) == length and check(qv)
            agg["num_total"] += 1
            agg["num_ocr_checksum"] += ocr_ok
            agg["num_qwen_checksum"] += q_ok
            if qv:
                agg["num_compared"] += 1
                agg["num_agree"] += qv in cands
            num_note = f"OCR КС {'✓' if ocr_ok else '✗'}, Qwen КС {'✓' if q_ok else '✗'}, совпали {'✓' if qv in cands else '✗'}"
        else:
            sn = re.sub(r"\D", "", (raw(data, "series") or "") + (raw(data, "number") or ""))
            agg["pass_total"] += 1
            joined = re.sub(r"\D", "", text)
            hit = len(sn) == 10 and (sn in cands or (sn[:4] in joined and sn[4:] in joined))
            agg["pass_series_number"] += hit
            num_note = f"серия+номер Qwen найдены в OCR {'✓' if hit else '✗'}"
        rows.append(
            f"| {s.short} | {s.kind} | {o['duration_ms'] / 1000:.1f} с | {cyr_share(text):.0%} | "
            f"{num_note} | {' '.join(name_hits)} |"
        )
        (CACHE / f"exp1-{s.short}.json").write_text(
            json.dumps(
                {"kind": s.kind, "ocr": text, "qwen": data},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    ocr_times = [o["duration_ms"] / 1000 for o in ocr if o["ok"]]
    lines = [
        "## Эксперимент 1. olmOCR на документах РФ",
        "",
        f"Образцов: {len(rf)} (паспорт РФ, СНИЛС, ИНН из открытого набора). OCR ответил: "
        f"{sum(o['ok'] for o in ocr)}/{len(rf)}; время p50 {pct(ocr_times, 0.5):.1f} с, p95 {pct(ocr_times, 0.95):.1f} с.",
        "",
        "| образец (sha256) | вид | время OCR | доля кириллицы в тексте | номер | ФИО Qwen найдено в OCR (Ф И О) |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        f"- Номер СНИЛС/ИНН с верной контрольной суммой среди кандидатов OCR: "
        f"**{agg['num_ocr_checksum']}/{agg['num_total']}**; у Qwen: **{agg['num_qwen_checksum']}/{agg['num_total']}**; "
        f"значение Qwen есть среди кандидатов OCR: {agg['num_agree']}/{agg['num_compared']}.",
        f"- Паспорт РФ: серия и номер Qwen подтверждены текстом OCR: {agg['pass_series_number']}/{agg['pass_total']}.",
        f"- ФИО: части ФИО, прочитанные Qwen, дословно найдены в тексте OCR: "
        f"**{agg['name_agree']}/{agg['name_total']}**.",
        "",
    ]
    return lines, agg


# ---------------- эксперимент 2: скорость и валидность Qwen ----------------


async def experiment2(gw: Gateway, samples: list[Sample]) -> tuple[list[str], dict[str, Any]]:
    picked = (
        [s for s in samples if s.kind in ("ru_passport", "snils", "inn")]
        + [s for s in samples if s.kind == "work_patent"]
    )[:20]
    sem = asyncio.Semaphore(4)  # как несколько параллельных задач воркера

    async def one(s: Sample) -> dict[str, Any]:
        async with sem:
            return await timed_extract(gw, page_png(s.path), s.kind)

    started = time.monotonic()
    results = await asyncio.gather(*(one(s) for s in picked))
    wall = time.monotonic() - started
    ok = [r for r in results if r["ok"]]
    times = [r["duration_ms"] / 1000 for r in ok]
    statuses: dict[str, int] = {}
    classified = 0
    for s, r in zip(picked, results, strict=True):
        if r["ok"]:
            classified += r["data"]["doc_type"] == s.kind
            for f in r["data"]["fields"].values():
                statuses[f["status"]] = statuses.get(f["status"], 0) + 1
        (CACHE / f"exp2-{s.short}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    errors = [r.get("code") for r in results if not r["ok"]]
    agg = {
        "n": len(picked),
        "ok": len(ok),
        "p50": pct(times, 0.5),
        "p95": pct(times, 0.95),
        "wall": wall,
        "retries": sum(r["inner_retries"] for r in ok),
        "classified": classified,
        "tokens_out_max": max((r["tokens_out"] or 0 for r in ok), default=0),
    }
    lines = [
        "## Эксперимент 2. Qwen3.8 на llm.ilons.ru: скорость и валидность",
        "",
        f"Документов: {agg['n']} (паспорт РФ, СНИЛС, ИНН, патент), параллельно по 4, изображение до 1600 px.",
        "",
        "| метрика | значение |",
        "|---|---|",
        f"| валидных ответов по схеме | {agg['ok']}/{agg['n']} |",
        f"| ошибки | {', '.join(map(str, errors)) or 'нет'} |",
        f"| p50 / p95 на документ | {agg['p50']:.1f} с / {agg['p95']:.1f} с |",
        f"| всего на {agg['n']} документов | {wall:.0f} с |",
        f"| повторов внутри шлюза (схема/обрезка) | {agg['retries']} |",
        f"| максимум токенов в ответе | {agg['tokens_out_max']} (рассуждения нет — иначе были бы тысячи) |",
        f"| вид документа определён верно | {classified}/{agg['ok']} |",
        f"| статусы полей | {', '.join(f'{k}: {v}' for k, v in sorted(statuses.items()))} |",
        "",
    ]
    return lines, agg


# ---------------- эксперимент 3: схемы всех восьми видов ----------------


async def experiment3(gw: Gateway, samples: list[Sample]) -> tuple[list[str], dict[str, Any]]:
    registry = gw.registry
    by_kind = {s.kind: s for s in samples}
    # Для видов без образцов в наборе — паспорт с МЧЗ (проверяем саму схему и честный doc_type).
    stand_in = by_kind.get("in_passport") or samples[0]
    rows, accepted = [], 0
    for doc_type in registry.documents:
        s = by_kind.get(doc_type, stand_in)
        r = await timed_extract(gw, page_png(s.path), doc_type)
        accepted += r["ok"]
        got = r["data"]["doc_type"] if r["ok"] else r.get("code")
        mrz = len(r["data"]["mrz_lines"]) if r["ok"] else "—"
        rows.append(
            f"| {doc_type} | {len(registry.documents[doc_type].fields)} | {s.kind} | "
            f"{'да' if r['ok'] else 'нет'} | {r.get('json_mode', '—')} | {got} | {mrz} | "
            f"{r['duration_ms'] / 1000:.1f} с |"
        )

    # Запасные режимы: тот же документ в json_object и prompt — работают ли они, если сервер сменится.
    snils = by_kind.get("snils", samples[0])
    modes = []
    for mode in ("json_object", "prompt"):
        gw.json_mode = mode  # type: ignore[assignment]
        r = await timed_extract(gw, page_png(snils.path), "snils")
        modes.append(f"| {mode} | {'да' if r['ok'] else r.get('code')} | {r['duration_ms'] / 1000:.1f} с |")
    gw.json_mode = "json_schema"

    lines = [
        "## Эксперимент 3. Structured outputs для схем всех восьми видов",
        "",
        'Схема плоская: `["string","null"]`, `enum` с `null`, `minItems/maxItems` у рамки, без `pattern`.',
        "",
        "| вид (схема) | полей | изображение | ответ по схеме | режим | doc_type в ответе | строк МЧЗ | время |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "Запасные режимы на СНИЛС:",
        "",
        "| LLM_JSON_MODE | ответ по схеме | время |",
        "|---|---|---|",
        *modes,
        "",
    ]
    return lines, {"accepted": accepted, "total": len(registry.documents)}


async def main(sha: str) -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    gw = Gateway(settings, get_registry())
    health = await gw.health()
    samples = load_samples()
    if not samples:
        print("data/open пуст: положите открытый набор или сгенерируйте синтетику")
        return 1
    print(f"модель доступна: {health.llm_ok}, в /models: {health.llm_model_listed}, OCR: {health.ocr_ok}")
    l1, a1 = await experiment1(gw, samples)
    print("эксперимент 1:", a1)
    l2, a2 = await experiment2(gw, samples)
    print(
        "эксперимент 2:",
        {k: round(v, 1) if isinstance(v, float) else v for k, v in a2.items()},
    )
    l3, a3 = await experiment3(gw, samples)
    print("эксперимент 3:", a3)
    await gw.aclose()

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    header = [
        f"# Эксперименты фазы 0 — {stamp}, коммит {sha}",
        "",
        f"- Модель: `{settings.LLM_MODEL}` @ `{gw.llm_host}`, режим `{settings.LLM_JSON_MODE}`, thinking выключен, "
        f"temperature 0. OCR: olmOCR @ `{gw.ocr_host}`.",
        "- Данные: открытый набор `data/open/` (реальные образцы, не синтетика). Образцы обозначены префиксом sha256; "
        "значения полей в отчёт не попадают.",
        "- Эталона ещё нет: правильность номеров меряем контрольными суммами, остальное — согласием Qwen и olmOCR.",
        "",
    ]
    report = REPORTS / f"{stamp}-{sha}-phase0-experiments.md"
    report.write_text("\n".join(header + l1 + l2 + l3), encoding="utf-8", newline="\n")
    print(f"отчёт: {report.relative_to(ROOT)}")
    return 0


def git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    return out.stdout.strip()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(git_sha())))
