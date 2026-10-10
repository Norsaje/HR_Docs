# E2E страницы соискателя (Playwright)

Запуск против поднятого стенда (`docker compose up -d --build`):

```bash
cd web/applicant
npm install
npx playwright install chromium
npx playwright test            # BASE_URL=https://<адрес> — если стенд не на localhost
```

Скриншоты экранов — в `e2e/screenshots/` (только синтетические картинки из `e2e/fixtures/`).
Прогон на файлах открытого набора кладёт скриншоты в `e2e/screenshots/private/` — эта папка в git не попадает.
