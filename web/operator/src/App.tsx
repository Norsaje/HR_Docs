// Каркас рабочего места: проверка входа (/api/auth/me), шапка, навигация по hash и баннер
// состояния модели. Маршруты: #/ — заявки (фильтры в query после #/?), #/app/<id> — карточка,
// #/queue — «В работе», #/expiring — истекающие, #/staff — сотрудники (supervisor и doc-editor своего офиса).
// Hash-навигация выбрана, чтобы Caddy отдавал один index.html без настройки маршрутов.
import { useEffect, useState } from "react";
import { api, ApiError, type Me, type ModelStatus } from "./api";
import { ApplicationsPage } from "./pages/Applications";
import { CardPage } from "./pages/Card";
import { QueuePage } from "./pages/Queue";
import { ExpiringPage } from "./pages/Expiring";
import { StaffPage } from "./pages/Staff";

// Заводить сотрудников могут руководитель и редактор (редактор — только в свой офис, проверяет сервер).
const canManageStaff = (me: { roles: string[] }) =>
  me.roles.includes("supervisor") || me.roles.includes("doc-editor");
import { ToastProvider } from "./ui";

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

/** Баннер «распознавание приостановлено»: опрос /api/operator/model-status раз в 30 с. */
function ModelBanner() {
  const [st, setSt] = useState<ModelStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get<ModelStatus>("/api/operator/model-status")
        .then((s) => alive && setSt(s))
        .catch(() => alive && setSt(null)); // эндпоинта может не быть — баннер просто не показываем
    load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  if (!st || (st.llm_ok && !st.paused)) return null;
  return (
    <div className="banner bad" role="alert">
      <strong>Распознавание приостановлено.</strong>{" "}
      {st.detail_ru ||
        "Модель недоступна: документы принимаются и ждут в очереди, разбор продолжится сам."}
    </div>
  );
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hash = useHash();

  useEffect(() => {
    api
      .get<Me>("/api/auth/me")
      .then(setMe)
      .catch((e: ApiError) => {
        if (e.status !== 401) setError(e.human);
      });
  }, []);

  async function logout() {
    try {
      const r = await api.post<{ logout_url?: string }>("/api/auth/logout");
      window.location.href = r?.logout_url || "/operator/";
    } catch {
      window.location.href = "/operator/";
    }
  }

  if (error) return <div className="center error-box">{error}</div>;
  if (!me) return <div className="center muted">Проверяем вход…</div>;

  const path = hash.replace(/^#/, "");
  let page = <ApplicationsPage />;
  let active = "apps";
  const cardMatch = path.match(/^\/app\/([^/?]+)/);
  if (cardMatch) {
    page = <CardPage id={cardMatch[1]} me={me} />;
  } else if (path.startsWith("/queue")) {
    page = <QueuePage />;
    active = "queue";
  } else if (path.startsWith("/expiring")) {
    page = <ExpiringPage />;
    active = "expiring";
  } else if (path.startsWith("/staff") && canManageStaff(me)) {
    page = <StaffPage />;
    active = "staff";
  }

  const roleRu = me.roles.includes("supervisor")
    ? "руководитель"
    : me.roles.includes("doc-editor")
      ? "редактор"
      : "просмотр";

  return (
    <ToastProvider>
      <div className="layout">
        <header className="topbar">
          <a className="brand" href="#/">
            <span className="brand-mark">HR</span> Приём документов
          </a>
          <nav>
            <a className={active === "apps" ? "active" : ""} href="#/">
              Заявки
            </a>
            <a className={active === "queue" ? "active" : ""} href="#/queue">
              В работе
            </a>
            <a
              className={active === "expiring" ? "active" : ""}
              href="#/expiring"
            >
              Истекающие
            </a>
            {canManageStaff(me) && (
              <a className={active === "staff" ? "active" : ""} href="#/staff">
                Сотрудники
              </a>
            )}
          </nav>
          <div className="user">
            <span className="user-name">{me.name}</span>
            <span className="user-role">
              {roleRu}
              {me.offices?.length ? ` · ${me.offices.join(", ")}` : ""}
            </span>
            <button className="btn small ghost" onClick={logout}>
              Выйти
            </button>
          </div>
        </header>
        <ModelBanner />
        <main className="content">{page}</main>
      </div>
    </ToastProvider>
  );
}
