"""Прогон открытого набора через настоящий API (`make open-set`, основа `make restart-drill`).

Каждый файл из data/open загружается в отдельную заявку в слот своего вида документа (вид берётся
из метки eval/dataset.py — это разметка набора, а не вход пайплайна: пайплайн получает только байты),
затем скрипт ждёт финальных статусов и печатает итог по видам. С флагом --drill посреди разбора
убивает воркер (docker compose kill worker), поднимает его снова и проверяет, что все файлы дошли.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import mailpit_code

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.dataset import load_samples  # noqa: E402

SLOT = {  # вид в наборе → (страна, слот документа)
    "ru_passport": ("RU", "ru_passport"),
    "snils": ("RU", "snils"),
    "inn": ("RU", "inn"),
    "ru_driver_license": ("RU", "ru_driver_license"),
    "work_patent": ("UZ", "work_patent"),
    "kg_driver_license": ("KG", "kg_driver_license"),
    "by_passport": ("BY", "by_passport"),
    "in_passport": ("IN", "in_passport"),
    # негативы: не тот разворот, чек, размытое и тёмное фото — в слоты документов РФ
    "ru_passport_registration": ("RU", "ru_passport"),
    "not_document": ("RU", "inn"),
    "unreadable_blurry": ("RU", "snils"),
    "unreadable_dark": ("RU", "snils"),
}
FINAL = {"ready", "needs_review", "unreadable", "failed"}


def main() -> int:
    drill = "--drill" in sys.argv
    limit = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), 0)
    base = "https://localhost"
    c = httpx.Client(base_url=base, verify=False, timeout=60)
    samples = [s for s in load_samples() if s.kind in SLOT]
    if limit:
        samples = samples[:limit]
    apps = []
    t0 = time.monotonic()
    for s in samples:
        country, slot = SLOT[s.kind]
        a = c.post(
            "/api/public/applications",
            json={
                "country_code": country,
                "full_name": "Прогон Набора",
                "email": "run@example.test",
                "consent_accepted": True,
            },
        ).json()
        mailpit_code.confirm(c, a)  # как соискатель: код из письма
        mime = "application/pdf" if s.path.suffix.lower() == ".pdf" else "image/jpeg"
        r = c.post(
            f"/api/public/applications/{a['application_id']}/files",
            data={"doc_type": slot},
            files={"files": (f"{s.short}{s.path.suffix}", s.path.read_bytes(), mime)},
            headers={"X-Application-Token": a["token"]},
        )
        res = (
            r.json()["results"][0]
            if r.status_code == 200
            else {"accepted": False, "error": r.json().get("error")}
        )
        c.post(
            f"/api/public/applications/{a['application_id']}/submit",
            headers={"X-Application-Token": a["token"]},
        )
        apps.append((s, slot, a, res))
    print(
        f"загружено {sum(1 for x in apps if x[3]['accepted'])}/{len(apps)} за {time.monotonic() - t0:.1f} с"
    )

    if drill:
        time.sleep(15)
        print("учения: docker compose kill worker посреди разбора")
        subprocess.run(["docker", "compose", "kill", "worker"], cwd=ROOT, check=True, capture_output=True)
        time.sleep(3)
        subprocess.run(["docker", "compose", "up", "-d", "worker"], cwd=ROOT, check=True, capture_output=True)
        print("воркер поднят снова")

    status: dict[str, str] = {}
    for _ in range(300):
        for s, slot, a, res in apps:
            if not res["accepted"] or status.get(s.sha256) in FINAL:
                continue
            st = c.get(
                f"/api/public/applications/{a['application_id']}", headers={"X-Application-Token": a["token"]}
            ).json()
            status[s.sha256] = next(d for d in st["documents"] if d["doc_type"] == slot)["status"]
        pending = [1 for s, _, _, res in apps if res["accepted"] and status.get(s.sha256) not in FINAL]
        if not pending:
            break
        time.sleep(3)

    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    for s, _, _, res in apps:
        by_kind[s.kind][status.get(s.sha256, "rejected" if not res["accepted"] else "pending")] += 1
    print(f"\nитого за {time.monotonic() - t0:.0f} с")
    print(f"{'вид в наборе':28} статусы")
    for kind, cnt in sorted(by_kind.items()):
        print(f"{kind:28} {dict(cnt)}")
    left = [s.short for s, _, _, res in apps if res["accepted"] and status.get(s.sha256) not in FINAL]
    print(f"не дошли до финального статуса: {len(left)}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
