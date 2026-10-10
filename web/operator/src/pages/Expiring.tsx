// Истекающие документы: список на N дней вперёд (уже истёкшие — сверху, красным) и выгрузка CSV.
// Файл CSV отдаёт API после проверки сессии — обычная ссылка, без токенов в адресе.
import { useEffect, useState } from "react";
import { api, errText, fmtDay, type ExpiringItem } from "../api";
import { Empty, ErrorBox, Skeleton } from "../ui";

export function ExpiringPage() {
  const [days, setDays] = useState(30);
  const [items, setItems] = useState<ExpiringItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    api
      .get<{ items: ExpiringItem[] }>(`/api/operator/expiring?days=${days}`)
      .then((r) => {
        setItems(r.items ?? []);
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, [days]);

  return (
    <section>
      <div className="page-head">
        <h1>Истекающие документы</h1>
        <span className="grow" />
        <label className="check">
          на ближайшие
          <select
            className="input"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            aria-label="Срок, дней"
          >
            {[7, 30, 60, 90, 365].map((d) => (
              <option key={d} value={d}>
                {d} дн.
              </option>
            ))}
          </select>
        </label>
        <a
          className="btn small primary"
          href={`/api/operator/expiring?days=${days}&format=csv`}
        >
          Скачать CSV
        </a>
      </div>
      {error && <ErrorBox text={error} />}
      {!items && !error && <Skeleton lines={5} height={32} />}
      {items && items.length === 0 && (
        <Empty
          title="Истекающих документов нет"
          hint="Здесь появятся документы, срок которых истёк или истекает в выбранный период."
        />
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
            {items.map((i, n) => (
              <tr
                key={n}
                className="clickable"
                onClick={() =>
                  (window.location.hash = `#/app/${i.application_id}`)
                }
              >
                <td className="mono">
                  <a href={`#/app/${i.application_id}`}>{i.public_no}</a>
                </td>
                <td>{i.full_name}</td>
                <td>{i.title}</td>
                <td>{fmtDay(i.expires_on)}</td>
                <td>
                  {i.days_left < 0 ? (
                    <span className="badge tone-bad">
                      истёк {Math.abs(i.days_left)} дн. назад
                    </span>
                  ) : (
                    <span
                      className={`badge ${i.days_left <= 7 ? "tone-warn" : "tone-info"}`}
                    >
                      {i.days_left} дн.
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
