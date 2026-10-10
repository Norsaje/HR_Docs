// Сквозной сценарий рабочего места оператора (Playwright, 1440×900):
// синтетическая заявка через публичное API → вход editor.msk через форму Keycloak → список и фильтры →
// карточка: подсветка рамки, правка (Enter), повторный разбор, горячие клавиши → «В работе» →
// «Истекающие» → выход; reader.msk не видит кнопок правки.
// Скриншоты: e2e/screenshots/*.png — только синтетика и экраны без документов;
// карточки с документами открытого набора — в e2e/screenshots/private/ (в git не попадают).
import {
  test,
  expect,
  type Page,
  request as pwRequest,
} from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const ROOT = path.resolve(HERE, "../../..");
const SHOTS = path.join(HERE, "screenshots");
const FIXTURE = path.join(HERE, "fixtures", "synthetic-passport.png");

function demoPassword(): string {
  const env = fs.readFileSync(path.join(ROOT, ".env"), "utf-8");
  const m = env.match(/^KC_DEMO_PASSWORD=(\S*)/m);
  if (!m || !m[1]) throw new Error("В .env нет KC_DEMO_PASSWORD");
  return m[1];
}

async function login(page: Page, username: string) {
  await page.goto("/operator/");
  await page.waitForURL(/\/kc\/realms\/hr\//);
  await page.fill("#username", username);
  await page.fill("#password", demoPassword());
  await page.click("#kc-login");
  await page.waitForURL(/\/operator\//);
  await expect(page.locator(".user-name")).toBeVisible();
}

async function shot(page: Page, name: string, priv = false) {
  await page.screenshot({
    path: path.join(SHOTS, priv ? "private" : "", name),
    fullPage: false,
  });
}

let appId = "";
let publicNo = "";

test.beforeAll(async () => {
  // Синтетическая заявка: вымышленный «паспорт» → настоящий разбор моделью.
  const api = await pwRequest.newContext({
    baseURL: "https://localhost",
    ignoreHTTPSErrors: true,
  });
  const r = await api.post("/api/public/applications", {
    data: {
      country_code: "RU",
      full_name: "Образцова Мария Тестовна",
      email: "obrazec@example.test",
      consent_accepted: true,
    },
  });
  expect(r.status()).toBe(201);
  const a = await r.json();
  appId = a.application_id;
  publicNo = a.public_no;
  const up = await api.post(`/api/public/applications/${appId}/files`, {
    headers: { "X-Application-Token": a.token },
    multipart: {
      doc_type: "ru_passport",
      files: {
        name: "obrazec.png",
        mimeType: "image/png",
        buffer: fs.readFileSync(FIXTURE),
      },
    },
  });
  expect(up.status()).toBe(200);
  await api.post(`/api/public/applications/${appId}/submit`, {
    headers: { "X-Application-Token": a.token },
  });
  for (let i = 0; i < 60; i++) {
    const st = await (
      await api.get(`/api/public/applications/${appId}`, {
        headers: { "X-Application-Token": a.token },
      })
    ).json();
    const doc = st.documents.find(
      (d: { doc_type: string }) => d.doc_type === "ru_passport",
    );
    if (["ready", "needs_review", "unreadable", "failed"].includes(doc.status))
      break;
    await new Promise((res) => setTimeout(res, 3000));
  }
});

test("редактор: список → карточка → правка → разбор → «В работе» → «Истекающие» → выход", async ({
  page,
}) => {
  await login(page, "editor.msk");
  await expect(page.locator("h1", { hasText: "Заявки" })).toBeVisible();
  await expect(page.locator("table.apps")).toBeVisible();
  await shot(page, "op-01-list.png");

  // Фильтры живут в адресе.
  await page.fill('input[aria-label="Поиск"]', publicNo);
  await expect(page).toHaveURL(new RegExp(`q=${encodeURIComponent(publicNo)}`));
  await expect(page.locator("table.apps tbody tr")).toHaveCount(1);
  await page.check('label.check:has-text("Только с ошибками") input');
  await expect(page).toHaveURL(/has_errors=1/);
  await page.uncheck('label.check:has-text("Только с ошибками") input');
  await shot(page, "op-02-filtered.png");

  // Карточка синтетической заявки.
  await page.click(`table.apps tbody tr:has-text("${publicNo}")`);
  await expect(page.locator(".card-grid")).toBeVisible();
  await expect(page.locator(".field").first()).toBeVisible({ timeout: 30_000 });
  const first = page.locator(".field").first();
  await first.click();
  await expect(page.locator(".viewer-canvas img")).toBeVisible();
  await page.locator('[data-testid="field-surname"]').hover();
  await shot(page, "op-03-card.png");

  // Правка поля: «Изменить» → значение → Enter → бейдж «правка оператора».
  const place = page.locator('[data-testid="field-birth_place"]');
  await place.click();
  await place.locator(".edit-btn").click();
  await place.locator("input").fill("ГОР. ПРИМЕРНОГОРСК (ПРАВКА)");
  await place.locator("input").press("Enter");
  await expect(page.locator(".toast.ok").first()).toBeVisible();
  await expect(
    page.locator('[data-testid="field-birth_place"] .edited-badge'),
  ).toBeVisible();
  await shot(page, "op-04-edited.png");

  // Горячие клавиши: J — следующее поле, ? — подсказка.
  await page.keyboard.press("j");
  await page.keyboard.press("?");
  await expect(
    page.locator('.modal[aria-label="Горячие клавиши"]'),
  ).toBeVisible();
  await shot(page, "op-05-hotkeys.png");
  await page.keyboard.press("Escape");

  // Повторный разбор.
  await page.click('button:has-text("Разобрать повторно")');
  await expect(page.locator(".toast").first()).toBeVisible();

  // История.
  await page.click('.tabs button:has-text("История")');
  await expect(page.locator(".history table")).toBeVisible();
  await shot(page, "op-06-history.png");

  // «В работе» и «Истекающие».
  await page.click('nav a:has-text("В работе")');
  await expect(page.locator(".tiles .tile").first()).toBeVisible();
  await shot(page, "op-07-queue.png");
  await page.click('nav a:has-text("Истекающие")');
  await expect(page.locator("h1", { hasText: "Истекающие" })).toBeVisible();
  await page.waitForTimeout(800);
  await shot(page, "op-08-expiring.png");

  // Карточка заявки из открытого набора (если есть) — только в private.
  await page.goto("/operator/#/");
  const other = page
    .locator(`table.apps tbody tr:not(:has-text("${publicNo}"))`)
    .first();
  if (await other.count()) {
    await other.click();
    await expect(page.locator(".card-grid")).toBeVisible();
    await page.waitForTimeout(1500);
    await shot(page, "op-real-card.png", true);
  }

  // Выход.
  await page.click('button:has-text("Выйти")');
  await page.waitForURL(
    (u) => !u.pathname.startsWith("/operator/") || u.hash === "",
    { timeout: 15_000 },
  );
});

test("reader.msk не видит кнопок правки", async ({ page }) => {
  await login(page, "reader.msk");
  await page.goto(`/operator/#/app/${appId}`);
  await expect(page.locator(".field").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".edit-btn")).toHaveCount(0);
  await expect(
    page.locator('button:has-text("Разобрать повторно")'),
  ).toHaveCount(0);
  await expect(page.locator("text=роль «просмотр»")).toBeVisible();
  await shot(page, "op-09-reader.png");
});
