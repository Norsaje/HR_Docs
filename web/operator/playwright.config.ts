// Playwright для рабочего места оператора: стенд https://localhost (самоподписанный сертификат),
// десктопный экран 1440×900. Запуск: npx playwright test (стенд должен быть поднят).
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost",
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 900 },
    locale: "ru-RU",
  },
});
