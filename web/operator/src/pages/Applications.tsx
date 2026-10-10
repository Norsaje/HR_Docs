// Список заявок своего офиса. Фильтры живут в адресе (#/?status=…&country=…&q=…&has_errors=1),
// поэтому ссылку на отфильтрованный список можно переслать коллеге. Автообновление раз в 10 с.
// Email приходит с сервера уже маскированным; номера документов в списке не показываются.
import { useCallback, useEffect, useState } from "react";
import { api, errText, fmtDate, statusTone, type AppListItem } from "../api";
import { Empty, ErrorBox, Skeleton, StatusBadge } from "../ui";

const STATUSES: [string, string][] = [
  ["", "Все статусы"],
  ["submitted", "Отправлена"],
  ["processing", "Разбирается"],
  ["needs_review", "Есть замечания"],
  ["ready", "Готово"],
];

function readFilters(): Record<string, string> {
  const q = window.location.hash.split("?")[1] ?? "";
  const p = new URLSearchParams(q);
  return {
    status: p.get("status") ?? "",
    country: p.get("country") ?? "",
    q: p.get("q") ?? "",
    has_errors: p.get("has_errors") ?? "",
  };
}

function writeFilters(f: Record<string, string>) {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => v && p.set(k, v));
  const qs = p.toString();
  window.history.replaceState(null, "", "#/" + (qs ? "?" + qs : ""));
}

export function ApplicationsPage() {
  const [filters, setFilters] = useState(readFilters);
  const [items, setItems] = useState<AppListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => v && p.set(k, v));
    api
      .get<{ items: AppListItem[] }>(
        "/api/operator/applications?" + p.toString(),
      )
      .then((r) => {
        let list = r.items ?? [];
        if (filters.has_errors) list = list.filter((i) => i.has_errors); // на случай, если сервер фильтр не знает
        setItems(list);
        setError(null);
        setUpdatedAt(new Date());
      })
      .catch((e) => setError(errText(e)));
  }, [filters]);

  useEffect(() => {
    writeFilters(filters);
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [filters, load]);

  const set = (k: string, v: string) => setFilters((f) => ({ ...f, [k]: v }));
  const countries = Array.from(
    new Set((items ?? []).map((i) => i.country_code)),
  ).sort();

  return (
    <section>
      <div className="page-head">
        <h1>Заявки</h1>
        <span className="muted small">
          {items ? `${items.length} шт.` : ""}{" "}
          {updatedAt
            ? `· обновлено ${updatedAt.toLocaleTimeString("ru-RU")}`
            : ""}
        </span>
      </div>
      <div className="filters">
        <input
          className="input grow"
          placeholder="Поиск: ФИО или номер заявки"
          value={filters.q}
          onChange={(e) => set("q", e.target.value)}
          aria-label="Поиск"
        />
        <select
          className="input"
          value={filters.status}
          onChange={(e) => set("status", e.target.value)}
          aria-label="Статус"
        >
          {STATUSES.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <input
          className="input narrow"
          placeholder="Страна (RU)"
          value={filters.country}
          list="countries"
          onChange={(e) =>
            set("country", e.target.value.toUpperCase().slice(0, 2))
          }
          aria-label="Страна"
        />
        <datalist id="countries">
          {countries.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        <label className="check">
          <input
            type="checkbox"
            checked={!!filters.has_errors}
            onChange={(e) => set("has_errors", e.target.checked ? "1" : "")}
          />
          Только с ошибками
        </label>
        {(filters.q ||
          filters.status ||
          filters.country ||
          filters.has_errors) && (
          <button
            className="btn small ghost"
            onClick={() =>
              setFilters({ status: "", country: "", q: "", has_errors: "" })
            }
          >
            Сбросить
          </button>
        )}
      </div>

      {error && <ErrorBox text={error} onRetry={load} />}
      {!items && !error && <Skeleton lines={6} height={36} />}
      {items && items.length === 0 && (
        <Empty
          title="Заявок нет"
          hint={
            filters.q || filters.status || filters.country || filters.has_errors
              ? "Под фильтры ничего не подошло — сбросьте фильтры."
              : "Как только соискатель отправит комплект, заявка появится здесь."
          }
        />
      )}
      {items && items.length > 0 && (
        <table className="table apps">
          <thead>
            <tr>
              <th>Заявка</th>
              <th>Соискатель</th>
              <th>Страна</th>
              <th>Документы</th>
              <th>Статус</th>
              <th>Создана</th>
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr
                key={a.id}
                className="clickable"
                onClick={() => (window.location.hash = `#/app/${a.id}`)}
              >
                <td>
                  <a href={`#/app/${a.id}`} className="mono">
                    {a.public_no}
                  </a>
                </td>
                <td>
                  <div>{a.full_name}</div>
                  <div className="muted small">{a.email_masked}</div>
                </td>
                <td>{a.country_code}</td>
                <td>
                  <div className="chips">
                    {(a.documents ?? []).map((d) => (
                      <span
                        key={d.doc_type}
                        className={`chip tone-${statusTone(d.status)}`}
                        title={d.status}
                      >
                        {d.title}
                        {d.errors ? (
                          <b className="chip-count">{d.errors}</b>
                        ) : null}
                      </span>
                    ))}
                    {!a.documents && (
                      <span className="muted small">
                        готово {a.documents_ready} из {a.documents_total}
                      </span>
                    )}
                  </div>
                </td>
                <td>
                  <StatusBadge status={a.status} />
                  {(a.errors_count ?? (a.has_errors ? 1 : 0)) > 0 && (
                    <span
                      className="err-count"
                      title="Замечаний уровня «ошибка»"
                    >
                      {a.errors_count ?? "!"} ош.
                    </span>
                  )}
                </td>
                <td className="muted small">{fmtDate(a.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
