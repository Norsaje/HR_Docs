#!/bin/bash
# Создаёт БД и роли при ПЕРВОМ старте PostgreSQL (выполняется только на пустом volume).
#   hr_owner  — владелец БД hr, под ним работают только миграции Alembic;
#   hr_app    — роль api и worker; права на таблицы выдаёт миграция (на audit_events только SELECT, INSERT);
#   keycloak  — владелец отдельной БД keycloak.
# Пароли приходят из env (.env), в репозитории их нет.
set -euo pipefail

: "${HR_OWNER_PASSWORD:?HR_OWNER_PASSWORD не задан}"
: "${HR_APP_PASSWORD:?HR_APP_PASSWORD не задан}"
: "${KC_DB_PASSWORD:?KC_DB_PASSWORD не задан}"

# Пароли передаём через psql-переменные (:'var'), чтобы они корректно экранировались.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$HR_OWNER_PASSWORD" -v app_pw="$HR_APP_PASSWORD" -v kc_pw="$KC_DB_PASSWORD" <<'SQL'
CREATE ROLE hr_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE hr_app LOGIN PASSWORD :'app_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw';
CREATE DATABASE hr OWNER hr_owner ENCODING 'UTF8';
CREATE DATABASE keycloak OWNER keycloak ENCODING 'UTF8';
REVOKE ALL ON DATABASE hr FROM PUBLIC;
GRANT CONNECT ON DATABASE hr TO hr_app;
SQL

# В БД hr: схема public принадлежит hr_owner, hr_app может только пользоваться ею (не создавать объекты).
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname hr <<'SQL'
ALTER SCHEMA public OWNER TO hr_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO hr_app;
SQL

echo "init: БД hr и keycloak, роли hr_owner, hr_app, keycloak созданы"
