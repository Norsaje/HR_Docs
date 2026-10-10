# infra/llm — локальная модель (профиль `llm-local`)

`compose.llm-local.yml` поднимает vLLM с OpenAI-совместимым API. Приложение не меняется:
воркер ходит на адрес из `LLM_BASE_URL`, переключение — `make switch-llm TARGET=local|ilons`.

```bash
# рядом со стендом (воркер ходит на http://vllm:8000/v1)
docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local up -d vllm

# отдельно на GPU-сервере (порт только 127.0.0.1:8000, с другой машины — SSH-туннель)
cd ~/hr_llm && docker compose -p hr_llm -f compose.llm-local.yml --profile llm-local up -d

make switch-llm TARGET=local && make llm-smoke     # проверить
make switch-llm TARGET=ilons                       # вернуть сервер в контуре
```

Подробно, требования к железу, выбор весов и результат проверки на A10 — `docs/local-model.md`.
