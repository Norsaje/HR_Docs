// «В работе»: реальное состояние очереди из таблицы jobs (опрос каждые 3 с) и состояние модели.
// Плитки: ждёт, разбирается, разобрано за час, ошибок за час, среднее и p95 время, самая старая задача.
import { useEffect, useState } from "react";
import {
  api,
  errText,
  fmtDate,
  STAGE_RU,
  type ModelStatus,
  type QueueStats,
} from "../api";
import { Empty, ErrorBox, Skeleton } from "../ui";

function secs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v < 60) return `${v.toFixed(v < 10 ? 1 : 0)} с`;
  return `${Math.floor(v / 60)} мин ${Math.round(v % 60)} с`;
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className={`tile ${tone ?? ""}`}>
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
    </div>
  );
}

export function QueuePage() {
  const [q, setQ] = useState<QueueStats | null>(null);
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api
        .get<QueueStats>("/api/operator/queue")
        .then((r) => alive && (setQ(r), setError(null)))
        .catch((e) => alive && setError(errText(e)));
      api
        .get<ModelStatus>("/api/operator/model-status")
        .then((m) => alive && setModel(m))
        .catch(() => alive && setModel(null));
    };
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <section>
      <div className="page-head">
        <h1>В работе</h1>
        <span className="muted small">обновляется каждые 3 секунды</span>
      </div>
      {model && (
        <div
          className={`banner ${model.llm_ok && !model.paused ? "ok" : "bad"}`}
        >
          {model.llm_ok && !model.paused
            ? "Модель распознавания доступна"
            : "Распознавание приостановлено"}
          {model.ocr_ok === false
            ? " · OCR недоступен (поля будут жёлтыми вместо зелёных)"
            : model.ocr_ok
              ? " · OCR доступен"
              : ""}
          {model.detail_ru ? ` · ${model.detail_ru}` : ""}
        </div>
      )}
      {error && <ErrorBox text={error} />}
      {!q && !error && <Skeleton lines={3} height={70} />}
      {q && (
        <>
          <div className="tiles">
            <Tile
              label="ждут в очереди"
              value={q.waiting}
              tone={q.waiting > 10 ? "warn" : ""}
            />
            <Tile label="разбираются сейчас" value={q.processing} tone="info" />
            <Tile label="разобрано за час" value={q.done_last_hour} tone="ok" />
            <Tile
              label="ошибок за час"
              value={q.failed_last_hour}
              tone={q.failed_last_hour ? "bad" : ""}
            />
            <Tile
              label="среднее время разбора"
              value={secs(q.avg_duration_s)}
            />
            <Tile label="95% разборов быстрее" value={secs(q.p95_duration_s)} />
            <Tile
              label="самая старая в очереди"
              value={secs(q.oldest_waiting_s)}
            />
          </div>

          <h2>Разбирается сейчас</h2>
          {q.in_progress.length === 0 ? (
            <Empty
              title="Сейчас ничего не разбирается"
              hint="Новые файлы попадут сюда сразу после загрузки."
            />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Задача</th>
                  <th>Воркер</th>
                  <th>Этап</th>
                  <th>Попытка</th>
                  <th>Начата</th>
                </tr>
              </thead>
              <tbody>
                {q.in_progress.map((j) => (
                  <tr key={String(j.job_id)}>
                    <td className="mono">#{j.job_id}</td>
                    <td>{j.worker}</td>
                    <td>{STAGE_RU[j.stage] ?? j.stage}</td>
                    <td>{j.attempts}</td>
                    <td className="small">{fmtDate(j.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {q.retrying && q.retrying.length > 0 && (
            <>
              <h2>Ждут повтора</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th>Задача</th>
                    <th>Попыток</th>
                    <th>Причина</th>
                    <th>Повтор после</th>
                  </tr>
                </thead>
                <tbody>
                  {q.retrying.map((j) => (
                    <tr key={String(j.job_id)}>
                      <td className="mono">#{j.job_id}</td>
                      <td>{j.attempts}</td>
                      <td>{j.error}</td>
                      <td className="small">{fmtDate(j.run_after)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h2>Упавшие</h2>
          {q.failed.length === 0 ? (
            <Empty title="Упавших задач нет" />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Задача</th>
                  <th>Попыток</th>
                  <th>Причина</th>
                  <th>Когда</th>
                </tr>
              </thead>
              <tbody>
                {q.failed.map((j) => (
                  <tr key={String(j.job_id)}>
                    <td className="mono">#{j.job_id}</td>
                    <td>{j.attempts}</td>
                    <td>{j.error}</td>
                    <td className="small">{fmtDate(j.at)}</td>
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
