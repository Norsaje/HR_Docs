"""Подключение к PostgreSQL (asyncpg) и применение миграций.

Приложение работает под ролью hr_app; миграции — SQL-файлы app/db/migrations/NNN_*.sql —
применяются при старте api под владельцем схемы hr_owner, каждая один раз
(учёт в таблице schema_migrations). Голый SQL вместо ORM: запросы видны целиком и объяснимы.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from app.config import Settings

MIGRATIONS = Path(__file__).parent / "migrations"


def _dsn(settings: Settings, owner: bool = False) -> str:
    user = "hr_owner" if owner else "hr_app"
    pw = (settings.HR_OWNER_PASSWORD if owner else settings.HR_APP_PASSWORD).get_secret_value()
    return f"postgresql://{user}:{pw}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(_dsn(settings), min_size=1, max_size=10)


async def migrate(settings: Settings) -> list[str]:
    conn = await asyncpg.connect(_dsn(settings, owner=True))
    applied: list[str] = []
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, at timestamptz DEFAULT now())"
        )
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            applied.append(path.name)
    finally:
        await conn.close()
    return applied
