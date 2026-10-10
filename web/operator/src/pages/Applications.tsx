// Список заявок офиса с фильтрами (статус, страна, поиск). Email уже замаскирован сервером.
import { useEffect, useState } from "react";
import { api, ApiError, fmtDate, statusRu, type AppListItem } from "../api";

const STATUSES = ["", "submitted", "processing", "ready", "needs_review"];

export function ApplicationsPage() {
  const [items, setItems] = useState<AppListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [country, setCountry] = useState("");
  const [q, setQ] = useState("");

  function load() {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (country) params.set("country", country.trim().toUpperCase());
    if (q) params.set("q", q.trim());
    api
      .get<{ items: AppListItem[] }>(
        "/api/operator/applications?" + params.toString(),
      )
      .then((r) => {
        setItems(r.items);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }

  useEffect(load, [status]);

  return (
    <section>
      <h1>Заявки</h1>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <label>
          Статус
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s ? statusRu(s) : "все"}
              </option>
            ))}
          </select>
        </label>
        <label>
          Страна
          <input
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            placeholder="RU"
            maxLength={2}
            size={4}
          />
        </label>
        <label className="grow">
          Поиск
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="ФИО или номер заявки"
          />
        </label>
        <button className="btn" type="submit">
          Найти
        </button>
      </form>

      {error && <div className="error-box">{error}</div>}
      {items === null && !error && <div className="muted">Загрузка…</div>}
      {items && items.length === 0 && (
        <div className="muted">Заявок не найдено.</div>
      )}
      {items && items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Номер</th>
              <th>Соискатель</th>
              <th>Email</th>
              <th>Страна</th>
              <th>Статус</th>
              <th>Документы</th>
              <th>Создана</th>
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr
                key={a.id}
                className="clickable"
                onClick={() => (window.location.hash = "#/app/" + a.id)}
              >
                <td>
                  <a href={"#/app/" + a.id}>{a.public_no}</a>
                </td>
                <td>{a.full_name}</td>
                <td className="muted">{a.email_masked}</td>
                <td>{a.country_code}</td>
                <td>
                  <span className={"badge st-" + a.status}>
                    {statusRu(a.status)}
                  </span>
                  {a.has_errors && (
                    <span className="badge red"> есть ошибки</span>
                  )}
                </td>
                <td>
                  {a.documents_ready} / {a.documents_total}
                </td>
                <td>{fmtDate(a.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
