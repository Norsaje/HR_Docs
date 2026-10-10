"""Переключение провайдера модели (`make switch-llm TARGET=local|ilons`) правкой .env.

local — vLLM на своей GPU: LLM_BASE_URL = LOCAL_LLM_URL (по умолчанию http://vllm:8000/v1),
LLM_MODEL = LOCAL_LLM_SERVED_NAME, LLM_API_KEY = VLLM_API_KEY.
ilons — обратно на llm.ilons.ru: значения берутся из ILONS_LLM_*, которые скрипт сам сохраняет
при первом переключении на local (ключ ilons не теряется). Код приложения не меняется —
меняется только адрес в конфиге, затем пересоздаётся worker. Ключи в вывод не печатаются.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")
KEYS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
SECRET = {"LLM_API_KEY"}


def _value(rest: str) -> str:
    return re.split(r"\s+#", rest, maxsplit=1)[0].strip()


def read_env() -> tuple[list[str], dict[str, str]]:
    lines = ENV.read_text(encoding="utf-8").splitlines()
    values = {}
    for line in lines:
        m = LINE.match(line)
        if m:
            values[m.group(1)] = _value(m.group(2))
    return lines, values


def write_env(lines: list[str], updates: dict[str, str]) -> None:
    seen = set()
    out = []
    for line in lines:
        m = LINE.match(line)
        if m and m.group(1) in updates:
            line = f"{m.group(1)}={updates[m.group(1)]}"
            seen.add(m.group(1))
        out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    # Под `python -I` переменная PYTHONIOENCODING не действует, а консоль Windows — cp1251.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if target not in ("local", "ilons"):
        print("Использование: make switch-llm TARGET=local|ilons")
        return 2
    lines, env = read_env()
    updates: dict[str, str] = {}
    if target == "local":
        local_url = env.get("LOCAL_LLM_URL") or "http://vllm:8000/v1"
        if not env.get("VLLM_API_KEY"):
            print("Не задан VLLM_API_KEY в .env (make secrets)")
            return 1
        # Сохраняем текущий (ilons) адрес один раз, чтобы потом вернуться без ручного ввода ключа.
        if not env.get("ILONS_LLM_API_KEY") and env.get("LLM_BASE_URL") != local_url:
            updates.update({f"ILONS_{k}": env.get(k, "") for k in KEYS})
        updates.update({
            "LLM_BASE_URL": local_url,
            "LLM_MODEL": env.get("LOCAL_LLM_SERVED_NAME") or "local-qwen",
            "LLM_API_KEY": env["VLLM_API_KEY"],
        })
    else:
        if not env.get("ILONS_LLM_BASE_URL") or not env.get("ILONS_LLM_API_KEY"):
            print("Нет сохранённых ILONS_LLM_* в .env: впишите LLM_BASE_URL, LLM_MODEL, LLM_API_KEY вручную")
            return 1
        updates.update({k: env[f"ILONS_{k}"] for k in KEYS if env.get(f"ILONS_{k}")})

    changed = False
    for k in KEYS:
        old, new = env.get(k, ""), updates.get(k, env.get(k, ""))
        if old == new:
            print(f"{k}: без изменений")
        elif k in SECRET:
            print(f"{k}: изменён")
            changed = True
        else:
            print(f"{k}: {old or '—'} → {new}")
            changed = True
    write_env(lines, updates)
    if "--no-restart" in sys.argv:
        return 0
    if changed:
        print("Пересоздаю worker с новым адресом модели: docker compose up -d worker")
        subprocess.run(["docker", "compose", "up", "-d", "worker"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
