# infra/llm — локальная модель (профиль `llm-local`)

`compose.llm-local.yml` поднимает vLLM с OpenAI-совместимым API во внутренней сети `backend`.
Приложение не меняется: воркер ходит на адрес из `LLM_BASE_URL`.

```bash
# 1. В .env: LOCAL_LLM_MODEL=<репозиторий весов>, LOCAL_LLM_SERVED_NAME=local-qwen, VLLM_API_KEY (make secrets)
docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local up -d vllm
docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml logs -f vllm   # ждать "Application startup complete"

# 2. Переключить воркер: в .env
#    LLM_BASE_URL=http://vllm:8000/v1
#    LLM_MODEL=local-qwen          (= LOCAL_LLM_SERVED_NAME)
#    LLM_API_KEY=<значение VLLM_API_KEY>
docker compose up -d worker
```

Подробно, требования к железу и выбор весов — `docs/local-model.md`.
Статус: на A10 не проверено (стенд разработки без GPU), см. `docs/limitations.md`.
