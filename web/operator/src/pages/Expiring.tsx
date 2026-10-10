// Истекающие документы: список на N дней вперёд и выгрузка CSV (ссылка — файл отдаёт API после проверки сессии).
import { useEffect, useState } from "react";
import { api, ApiError, type ExpiringItem } from "../api";

export function ExpiringPage() {
  const [days, setDays] = useState(30);
  const [items, setItems] = useState<ExpiringItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    api
      .get<{ items: ExpiringItem[] }>(`/api/operator/expiring?days=${days}`)
      .then((r) => {
        setItems(r.items);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }, [days]);

  return (
    <section>
      <h1>Истекающие документы</h1>
      <div className="filters">
        <label>
          Срок, дней
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {[7, 30, 60, 90].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <a
          className="btn"
          href={`/api/operator/expiring?days=${days}&format=csv`}
        >
          Скачать CSV
        </a>
      </div>
      {error && <div className="error-box">{error}</div>}
      {items === null && !error && <div className="muted">Загрузка…</div>}
      {items && items.length === 0 && (
        <div className="muted">Истекающих документов нет.</div>
      )}
      {items && items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Заявка</th>
              <th>Соискатель</th>
              <th>Документ</th>
              <th>Действует до</th>
              <th>Осталось</th>
            </tr>
          </thead>
          <tbody>
            {items.map((x, i) => (
              <tr
                key={i}
                className="clickable"
                onClick={() =>
                  (window.location.hash = "#/app/" + x.application_id)
                }
              >
                <td>
                  <a href={"#/app/" + x.application_id}>{x.public_no}</a>
                </td>
                <td>{x.full_name}</td>
                <td>{x.title}</td>
                <td>{x.expires_on}</td>
                <td className={x.days_left < 0 ? "red-text" : ""}>
                  {x.days_left < 0
                    ? `истёк ${-x.days_left} дн. назад`
                    : `${x.days_left} дн.`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
