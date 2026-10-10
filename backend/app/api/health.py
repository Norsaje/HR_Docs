"""Проверки живости: /api/healthz — процесс жив; /api/readyz появится вместе с БД и S3.

healthz намеренно ничего не трогает снаружи: его дёргает healthcheck Docker каждые 10 с,
и падение Keycloak или модели не должно перезапускать api.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
