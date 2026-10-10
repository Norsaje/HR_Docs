// «В работе»: реальное состояние очереди jobs из БД, опрос каждые 3 с.
import { useEffect, useState } from "react";
import { api, ApiError, fmtDate, type QueueStats } from "../api";

function sec(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(1).replace(".", ",") + " с";
}

export function QueuePage() {
  const [q, setQ] = useState<QueueStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .get<QueueStats>("/api/operator/queue")
        .then((r) => {
          if (alive) {
            setQ(r);
            setError(null);
          }
        })
        .catch((e: ApiError) => alive && setError(e.message));
    tick();
    const t = window.setInterval(tick, 3000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  return (
    <section>
      <h1>В работе</h1>
      {error && <div className="error-box">{error}</div>}
      {!q && !error && <div className="muted">Загрузка…</div>}
      {q && (
        <>
          <div className="stats">
            <Stat label="Ждут разбора" value={q.waiting} />
            <Stat label="Разбираются" value={q.processing} />
            <Stat label="Разобрано за час" value={q.done_last_hour} />
            <Stat
              label="Ошибок за час"
              value={q.failed_last_hour}
              red={q.failed_last_hour > 0}
            />
            <Stat label="Среднее время" value={sec(q.avg_duration_s)} />
            <Stat label="p95 время" value={sec(q.p95_duration_s)} />
            <Stat
              label="Самая старая в очереди"
              value={sec(q.oldest_waiting_s)}
            />
          </div>
          <h2>Разбирается сейчас</h2>
          {q.in_progress.length === 0 ? (
            <div className="muted">Сейчас ничего не разбирается.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Задача</th>
                  <th>Файл</th>
                  <th>Воркер</th>
                  <th>Этап</th>
                  <th>Попытка</th>
                  <th>Начата</th>
                </tr>
              </thead>
              <tbody>
                {q.in_progress.map((j) => (
                  <tr key={j.job_id}>
                    <td className="mono">{String(j.job_id).slice(0, 8)}</td>
                    <td className="mono">{String(j.file_id).slice(0, 8)}</td>
                    <td>{j.worker}</td>
                    <td>{j.stage}</td>
                    <td>{j.attempts}</td>
                    <td>{fmtDate(j.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h2>Упавшие</h2>
          {q.failed.length === 0 ? (
            <div className="muted">Упавших задач нет.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Задача</th>
                  <th>Файл</th>
                  <th>Попыток</th>
                  <th>Причина</th>
                  <th>Когда</th>
                </tr>
              </thead>
              <tbody>
                {q.failed.map((j) => (
                  <tr key={j.job_id}>
                    <td className="mono">{String(j.job_id).slice(0, 8)}</td>
                    <td className="mono">{String(j.file_id).slice(0, 8)}</td>
                    <td>{j.attempts}</td>
                    <td>{j.error}</td>
                    <td>{fmtDate(j.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}

function Stat({
  label,
  value,
  red,
}: {
  label: string;
  value: number | string;
  red?: boolean;
}) {
  return (
    <div className={"stat" + (red ? " stat-red" : "")}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
