"""Отчёт по прогону открытого набора через стенд (после `make open-set`) → eval/reports/<дата>-open-set.md.

Считает по данным БД стенда (только файлы, загруженные после --since): статусы документов,
доли цветов полей, на чём держатся зелёные (контрольная сумма, МЧЗ, текстовый слой, olmOCR),
нечитаемые, время модели p50/p95 и полное время задачи. Значений полей в отчёте нет.
Эталонной разметки нет, поэтому «ложные зелёные» здесь не меряются — см. docs/limitations.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sql(query: str) -> list[list[str]]:
    out = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "hr",
            "-A",
            "-t",
            "-F",
            "\t",
            "-c",
            query,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def main() -> int:
    since = sys.argv[1]
    docs = sql(
        f"SELECT d.doc_type, d.status FROM documents d JOIN files f ON f.document_id=d.id "
        f"WHERE f.created_at >= '{since}'"
    )
    fields = sql(
        f"SELECT d.doc_type, fl.confidence, fl.signals::text FROM fields fl JOIN files f ON f.id=fl.file_id "
        f"JOIN documents d ON d.id=fl.document_id WHERE f.created_at >= '{since}' AND fl.source='model'"
    )
    runs = sql(
        f"SELECT r.duration_ms FROM extraction_runs r JOIN files f ON f.id=r.file_id WHERE f.created_at >= '{since}'"
    )
    jobs = sql(
        f"SELECT extract(epoch FROM j.finished_at - j.started_at) FROM jobs j JOIN files f "
        f"ON f.id::text = j.payload->>'file_id' WHERE f.created_at >= '{since}' AND j.status='done'"
    )
    events = sql(
        f"SELECT e.details::text FROM audit_events e JOIN files f ON f.id::text=e.object_id "
        f"WHERE f.created_at >= '{since}' AND e.action='parse.finished'"
    )
    unread = sql(
        f"SELECT e.message_ru FROM audit_events e JOIN files f ON f.id::text=e.object_id "
        f"WHERE f.created_at >= '{since}' AND e.action='parse.unreadable'"
    )

    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    for dt, st in docs:
        by_type[dt][st] += 1
    colors: dict[str, Counter[str]] = defaultdict(Counter)
    green_basis: Counter[str] = Counter()
    total = Counter()
    for dt, color, signals in fields:
        colors[dt][color] += 1
        total[color] += 1
        if color == "green":
            s = set(json.loads(signals))
            basis = next(
                (b for b in ("checksum_ok", "mrz_match", "text_layer_match", "ocr_agree") if b in s), "другое"
            )
            green_basis[basis] += 1
    ocr = Counter(json.loads(d).get("ocr", "?") for (d,) in events)
    mrz_ok = sum(1 for (d,) in events if json.loads(d).get("mrz_valid"))
    model_s = [int(r[0]) / 1000 for r in runs]
    job_s = [float(r[0]) for r in jobs if r[0]]
    n_fields = sum(total.values()) or 1

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    lines = [
        f"# Прогон открытого набора через стенд — {stamp}, коммит {sha} (+ незакоммиченные правки пайплайна)",
        "",
        "Каждый файл `data/open/` загружен через публичное API в отдельную заявку, в слот своего вида "
        "(`scripts/run_open_set.py`); разбор — воркер стенда: проверка пригодности → Qwen3.8 (проход A) "
        "параллельно с olmOCR (проход B) → правила, контрольные суммы, МЧЗ → цвет поля (05-pipeline §2.7). "
        "Значений полей в отчёте нет. Эталона нет: точность и «ложные зелёные» не измерены.",
        "",
        "## Итог",
        "",
        "| метрика | значение |",
        "|---|---|",
        f"| документов (файлов) | {len(docs)} |",
        f"| полей (от модели) | {sum(total.values())} |",
        f"| зелёных | {total['green']} ({total['green'] / n_fields:.0%}) |",
        f"| жёлтых | {total['yellow']} ({total['yellow'] / n_fields:.0%}) |",
        f"| красных | {total['red']} ({total['red'] / n_fields:.0%}) |",
        f"| серых (необязательное поле отсутствует) | {total['grey']} ({total['grey'] / n_fields:.0%}) |",
        f"| зелёные держатся на | {', '.join(f'{k}: {v}' for k, v in green_basis.most_common()) or '—'} |",
        f"| olmOCR (проход B) | {', '.join(f'{k}: {v}' for k, v in ocr.items())} |",
        f"| МЧЗ с верными контрольными цифрами | {mrz_ok} документов |",
        f"| «нечитаемо» без вызова модели или «нет документа» | {len(unread)} |",
        f"| время модели (проход A) p50 / p95 | {pct(model_s, 0.5):.1f} с / {pct(model_s, 0.95):.1f} с |",
        f"| полное время задачи p50 / p95 | {pct(job_s, 0.5):.1f} с / {pct(job_s, 0.95):.1f} с |",
        "",
        "## По видам документов (слот загрузки)",
        "",
        "| вид | статусы документов | зелёные | жёлтые | красные | серые |",
        "|---|---|---|---|---|---|",
    ]
    for dt in sorted(by_type):
        c = colors[dt]
        lines.append(
            f"| {dt} | {', '.join(f'{k}: {v}' for k, v in sorted(by_type[dt].items()))} | "
            f"{c['green']} | {c['yellow']} | {c['red']} | {c['grey']} |"
        )
    lines += ["", "## Причины «нечитаемо»", ""] + [f"- {r[0]}" for r in unread] + [""]
    report = ROOT / "eval" / "reports" / f"{stamp}-open-set.md"
    report.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"отчёт: {report.relative_to(ROOT)}")
    print("\n".join(lines[6:22]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
