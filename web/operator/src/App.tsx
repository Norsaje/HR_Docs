// Каркас рабочего места: проверка входа (/api/auth/me), шапка и навигация по hash.
// Маршруты: #/ — заявки, #/app/<id> — карточка, #/queue — «В работе», #/expiring — истекающие.
// Hash-навигация выбрана, чтобы Caddy отдавал один index.html без настройки маршрутов.
import { useEffect, useState } from "react";
import { api, ApiError, type Me } from "./api";
import { ApplicationsPage } from "./pages/Applications";
import { CardPage } from "./pages/Card";
import { QueuePage } from "./pages/Queue";
import { ExpiringPage } from "./pages/Expiring";

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
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
        if (e.status !== 401) setError(e.message);
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
  }

  return (
    <div className="layout">
      <header className="topbar">
        <div className="brand">Приём документов · оператор</div>
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
        </nav>
        <div className="user">
          <span title={me.roles.join(", ")}>{me.name}</span>
          <button className="btn small" onClick={logout}>
            Выйти
          </button>
        </div>
      </header>
      <main className="content">{page}</main>
    </div>
  );
}
