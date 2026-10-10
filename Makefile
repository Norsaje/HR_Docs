# Makefile — единая точка входа для команд стенда (список целей — docs/spec/10-eval-ops.md).
# Рецепты пишутся под bash: на Linux он есть всегда, на Windows — из Git for Windows.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PY ?= $(shell command -v python3 >/dev/null 2>&1 && echo python3 || echo python)
COMPOSE := docker compose
LLM_LOCAL := -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local

.PHONY: help secrets up down logs ps test lint api-types e2e-smoke restart-drill open-set test-files demo-seed eval \
        experiments llm-smoke switch-llm kc-admin-tunnel-help

help: ## Показать цели
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-22s %s\n", $$1, $$2}'

secrets: ## Заполнить пустые секреты в .env случайными значениями
	@PYTHONIOENCODING=utf-8 $(PY) -I scripts/gen_secrets.py

up: ## Поднять стенд (сборка + запуск)
	$(COMPOSE) up -d --build

down: ## Остановить стенд (данные в volume сохраняются)
	$(COMPOSE) down

logs: ## Логи всех сервисов
	$(COMPOSE) logs -f --tail=200

ps: ## Состояние сервисов
	$(COMPOSE) ps

test: ## Юнит- и интеграционные тесты (PostgreSQL из профиля test)
	$(COMPOSE) --profile test up -d --wait postgres-test
	cd backend && uv run pytest -q

lint: ## ruff, mypy, eslint, tsc
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	@if [ -d web/applicant/node_modules ]; then cd web/applicant && npm run lint && npx tsc --noEmit; fi
	@if [ -d web/operator/node_modules ]; then cd web/operator && npm run lint && npx tsc --noEmit; fi

api-types: ## OpenAPI бэкенда -> TS-типы для обоих фронтендов
	@echo "не реализовано (фаза 1)"; exit 1

e2e-smoke: ## Сквозной сценарий: заявка -> файл -> разбор -> вход оператора -> 401 без cookie
	cd backend && PYTHONIOENCODING=utf-8 uv run python -W ignore ../scripts/e2e_smoke.py

restart-drill: ## 20 файлов, убить воркер посреди разбора, поднять, все дошли до финального статуса
	cd backend && PYTHONIOENCODING=utf-8 uv run python -W ignore ../scripts/run_open_set.py --drill --limit=20

test-files: ## Тестовые файлы для ручной проверки в data/test (виды документов, fake, big, blurry, dark, истёкшие)
	cd backend && PYTHONIOENCODING=utf-8 uv run python -W ignore ../scripts/make_test_files.py

demo-seed: ## Демо-заявки через настоящий API и модель (RU, UZ, BY) из data/test
	cd backend && PYTHONIOENCODING=utf-8 uv run python -W ignore ../scripts/demo_seed.py

open-set: ## Прогнать весь открытый набор через API и показать статусы по видам документов
	cd backend && PYTHONIOENCODING=utf-8 uv run python -W ignore ../scripts/run_open_set.py

eval: ## Оценка качества: make eval [SPLIT=dev|holdout|distorted|all] [LIMIT=N]
	@echo "не реализовано (фаза 2)"; exit 1

experiments: ## Эксперименты фазы 0 с моделью и OCR (eval/experiments)
	cd backend && PYTHONIOENCODING=utf-8 uv run python ../eval/experiments/phase0.py

llm-smoke: ## Один синтетический документ через текущий LLM_BASE_URL (изнутри контейнера worker)
	docker compose exec -T worker python -m app.llm.smoke

switch-llm: ## Переключить провайдера модели: make switch-llm TARGET=local|ilons (правит .env, пересоздаёт worker)
	@P=$$(python3 -c "" >/dev/null 2>&1 && echo python3 || echo python); $$P -I scripts/switch_llm.py $(TARGET)

kc-admin-tunnel-help: ## Команды SSH-туннеля к админке Keycloak и Mailpit
	@echo "Админка Keycloak и Mailpit слушают только 127.0.0.1 стенда."
	@echo "  ssh -L 8081:127.0.0.1:8081 -L 8025:127.0.0.1:8025 <пользователь>@<стенд>"
	@echo "  затем: http://localhost:8081/kc/admin  и  http://localhost:8025"
