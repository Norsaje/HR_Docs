// Сквозные тесты страницы соискателя на «телефоне» 390×844 против живого стенда (https://localhost).
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL ?? "https://localhost",
    ignoreHTTPSErrors: true, // стенд с самоподписанным сертификатом (CADDY_TLS=internal)
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    locale: "ru-RU",
  },
});
