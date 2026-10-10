// Сценарий соискателя С1–С3 на телефоне: страна → набор документов → загрузка нескольких файлов →
// дубликат и переименованный .exe дают понятный отказ → отправка → статусы доходят до финальных →
// «Не хватает» → дозагрузка в ту же заявку → после перезагрузки страница возвращает на заявку.
// В репозиторий идут только скриншоты с синтетическими картинками (e2e/fixtures), без ПДн.
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)); // пакет — ES-модуль, HERE нет

const FIX = path.join(HERE, "fixtures");
const SHOTS = path.join(HERE, "screenshots");
const FINAL = /Готово|Принят, есть замечания|Не прочитался|Ошибка разбора/;

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: path.join(SHOTS, `${name}.png`),
    fullPage: true,
  });
}

// Нет горизонтального скролла и ни один элемент не вылезает за край экрана
// (overflow-x: hidden у body спрятал бы скролл, но не обрезанную кнопку).
async function noHorizontalScroll(page: Page) {
  const r = await page.evaluate(() => {
    const w = document.documentElement.clientWidth;
    const over = Array.from(document.querySelectorAll("body *"))
      .filter(
        (el) =>
          el.getBoundingClientRect().width > 0 &&
          el.getBoundingClientRect().right > w + 1,
      )
      .map((el) => `${el.tagName}.${(el as HTMLElement).className}`);
    return {
      scroll: document.documentElement.scrollWidth - w,
      over: over.slice(0, 5),
    };
  });
  expect(r.scroll, "горизонтальный скролл").toBeLessThanOrEqual(0);
  expect(r.over, "элементы за краем экрана").toEqual([]);
}

test("соискатель: полный путь на телефоне", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Кто вы" })).toBeVisible();
  await shot(page, "01-start");

  // Поиск страны и набор документов
  await page.getByLabel("Поиск страны").fill("Росс");
  await page.getByRole("option", { name: "Россия" }).click();
  await expect(page.getByText("Понадобятся документы:")).toBeVisible();
  await page.getByPlaceholder("Как в паспорте").fill("Образцов Иван Петрович");
  await page.getByPlaceholder("name@example.com").fill("obrazec@example.test");

  // Без согласия — понятный отказ, заявка не создаётся
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByRole("alert")).toContainText("согласие");
  await page.getByRole("checkbox").check();
  await shot(page, "02-form");
  await page.getByRole("button", { name: "Продолжить" }).click();

  // Шаг 2: документы
  await expect(page.getByTestId("public-no")).toBeVisible();
  await expect(page.getByTestId("doc-ru_passport")).toBeVisible();
  await noHorizontalScroll(page);
  await shot(page, "03-documents");

  const passport = page.getByTestId("doc-ru_passport");
  await page
    .getByTestId("input-ru_passport")
    .setInputFiles([
      path.join(FIX, "synthetic-card.png"),
      path.join(FIX, "synthetic-card-2.png"),
    ]);
  await expect(
    passport.getByTestId("upload-result").filter({ hasText: "Принят" }),
  ).toHaveCount(2);

  // Тот же файл ещё раз и .exe под видом фото — отказ с причиной по-русски
  await page
    .getByTestId("input-ru_passport")
    .setInputFiles(path.join(FIX, "synthetic-card.png"));
  await expect(
    passport.getByText("Отклонён: Этот файл уже загружен в заявку."),
  ).toBeVisible();
  await page
    .getByTestId("input-ru_passport")
    .setInputFiles(path.join(FIX, "fake-photo.jpg"));
  await expect(
    passport.getByText(/Отклонён: Тип файла не подходит/),
  ).toBeVisible();
  await noHorizontalScroll(page);
  await shot(page, "04-uploads");

  // Отправка — мгновенное подтверждение с номером
  await page.getByRole("button", { name: "Отправить комплект" }).click();
  await expect(
    page.getByRole("heading", { name: /Комплект отправлен/ }),
  ).toBeVisible({ timeout: 5000 });
  await expect(page.getByText("Не хватает:").first()).toBeVisible();
  await shot(page, "05-submitted");

  // Статусы доходят до финальных без перезагрузки
  await expect(passport.getByTestId("doc-status")).toContainText(FINAL, {
    timeout: 180_000,
  });
  await shot(page, "06-status");

  // Дозагрузка в ту же заявку из блока «Не хватает»
  await page
    .getByTestId("input-snils")
    .setInputFiles(path.join(FIX, "synthetic-card-3.png"));
  await expect(
    page.getByTestId("doc-snils").getByTestId("upload-result"),
  ).toContainText("Принят");
  await shot(page, "07-reupload");

  // После перезагрузки страница возвращает на заявку
  const no = await page.getByTestId("public-no").textContent();
  await page.reload();
  await expect(page.getByTestId("public-no")).toHaveText(no ?? "");
  await expect(
    page.getByTestId("doc-snils").getByTestId("doc-status"),
  ).not.toContainText("Не загружен");
  await page.setViewportSize({ width: 360, height: 780 });
  await noHorizontalScroll(page);
  await shot(page, "08-after-reload-360");
});

// Тот же путь на реальных файлах открытого набора — скриншоты только в screenshots/private (в git не идут).
test("соискатель: документы из открытого набора", async ({ page }) => {
  const open = path.resolve(HERE, "../../../data/open/Первый пакет");
  test.skip(!fs.existsSync(open), "data/open нет");
  const files = fs.readdirSync(open);
  const pick = (prefix: string) =>
    path.join(open, files.find((f) => f.startsWith(prefix))!);
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/");
  await page.getByLabel("Поиск страны").fill("Росс");
  await page.getByRole("option", { name: "Россия" }).click();
  await page
    .getByPlaceholder("Как в паспорте")
    .fill("Проверка Открытого Набора");
  await page.getByPlaceholder("name@example.com").fill("open@example.test");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page
    .getByTestId("input-ru_passport")
    .setInputFiles(pick("Паспорт РФ К"));
  await page.getByTestId("input-snils").setInputFiles(pick("СНИЛС К"));
  await page.getByTestId("input-inn").setInputFiles(pick("ИНН К"));
  await expect(
    page.getByTestId("upload-result").filter({ hasText: "Принят" }),
  ).toHaveCount(3);
  await page.getByRole("button", { name: "Отправить комплект" }).click();
  for (const dt of ["ru_passport", "snils", "inn"]) {
    await expect(
      page.getByTestId(`doc-${dt}`).getByTestId("doc-status"),
    ).toContainText(FINAL, {
      timeout: 180_000,
    });
  }
  await page.screenshot({
    path: path.join(SHOTS, "private", "open-set-status.png"),
    fullPage: true,
  });
});
