// Карточка заявки: слева документы, в центре оригинал страницы с рамкой выбранного поля,
// справа поля с цветом уверенности (сначала красные, потом жёлтые) и правкой по клику.
// Вкладка «История» — журнал аудита по заявке. Права проверяет сервер: при 403 — понятный текст.
import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  fmtDate,
  statusRu,
  type Card,
  type Doc,
  type Field,
  type HistoryItem,
  type Me,
} from "../api";

const CONF_ORDER: Record<string, number> = {
  red: 0,
  yellow: 1,
  green: 2,
  gray: 3,
};
const CONF_RU: Record<string, string> = {
  red: "нужна проверка",
  yellow: "стоит взглянуть",
  green: "подтверждено",
  gray: "нет на документе",
};
const SEV_RU: Record<string, string> = {
  error: "Ошибка",
  warning: "Внимание",
  info: "Заметка",
};

export function CardPage({ id, me }: { id: string; me: Me }) {
  const [card, setCard] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [docId, setDocId] = useState<string | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [fieldId, setFieldId] = useState<string | null>(null);
  const [tab, setTab] = useState<"fields" | "history">("fields");
  const canEdit = me.roles.some(
    (r) => r === "doc-editor" || r === "supervisor",
  );

  function load() {
    api
      .get<Card>("/api/operator/applications/" + id)
      .then((c) => {
        setCard(c);
        setError(null);
        const doc = c.documents.find((d) => d.id === docId) ?? c.documents[0];
        if (doc) {
          setDocId(doc.id);
          if (!fileId || !doc.files.some((f) => f.file_id === fileId))
            setFileId(doc.files[0]?.file_id ?? null);
        }
      })
      .catch((e: ApiError) =>
        setError(
          e.status === 404
            ? "Заявка не найдена или недоступна вашему офису."
            : e.message,
        ),
      );
  }

  useEffect(load, [id]);

  const doc: Doc | undefined = card?.documents.find((d) => d.id === docId);
  const fields = useMemo(
    () =>
      [...(doc?.fields ?? [])].sort(
        (a, b) =>
          (CONF_ORDER[a.confidence] ?? 9) - (CONF_ORDER[b.confidence] ?? 9),
      ),
    [doc],
  );
  const selected = fields.find((f) => f.id === fieldId);

  function selectDoc(d: Doc) {
    setDocId(d.id);
    setFileId(d.files[0]?.file_id ?? null);
    setFieldId(null);
  }

  function selectField(f: Field) {
    setFieldId(f.id);
    if (f.file_id) setFileId(f.file_id);
  }

  async function reprocess(d: Doc) {
    try {
      await api.post("/api/operator/documents/" + d.id + "/reprocess");
      setNotice("Документ поставлен на повторный разбор.");
      load();
    } catch (e) {
      setNotice(
        (e as ApiError).status === 403
          ? "Нет прав на повторный разбор."
          : (e as ApiError).message,
      );
    }
  }

  if (error) return <div className="error-box">{error}</div>;
  if (!card) return <div className="muted">Загрузка карточки…</div>;
  const a = card.application;
  const appIssues = [...(card.issues ?? []), ...(card.crosscheck ?? [])];

  return (
    <section className="card-page">
      <div className="card-head">
        <a href="#/">← К списку</a>
        <h1>
          Заявка {a.public_no} · {a.full_name}
        </h1>
        <span className={"badge st-" + a.status}>{statusRu(a.status)}</span>
        <span className="muted">
          {a.country_code} · {a.email_masked} · {fmtDate(a.created_at)}
        </span>
        <span className="spacer" />
        <a
          className="btn small"
          href={`/api/operator/applications/${a.id}/export?format=json`}
        >
          Выгрузить JSON
        </a>
        <a
          className="btn small"
          href={`/api/operator/applications/${a.id}/export?format=csv`}
        >
          Выгрузить CSV
        </a>
      </div>
      {notice && (
        <div className="notice" onClick={() => setNotice(null)}>
          {notice}
        </div>
      )}
      {appIssues.length > 0 && (
        <IssueList issues={appIssues} title="Сверка между документами" />
      )}

      <div className="card-grid">
        <aside className="docs">
          {card.documents.length === 0 && (
            <div className="muted">Документов нет.</div>
          )}
          {card.documents.map((d) => {
            const reds = d.fields.filter((f) => f.confidence === "red").length;
            return (
              <button
                key={d.id}
                className={"doc-item" + (d.id === docId ? " active" : "")}
                onClick={() => selectDoc(d)}
              >
                <div className="doc-title">{d.title}</div>
                <div className="doc-meta">
                  <span className={"badge st-" + d.status}>
                    {statusRu(d.status)}
                  </span>
                  {reds > 0 && <span className="badge red">{reds} красн.</span>}
                  <span className="muted">файлов: {d.files.length}</span>
                </div>
              </button>
            );
          })}
        </aside>

        <div className="viewer">
          {doc && doc.files.length > 1 && (
            <div className="file-tabs">
              {doc.files.map((f, i) => (
                <button
                  key={f.file_id}
                  className={
                    "btn small" + (f.file_id === fileId ? " primary" : "")
                  }
                  onClick={() => setFileId(f.file_id)}
                >
                  Файл {i + 1}
                </button>
              ))}
            </div>
          )}
          {doc?.files.find((f) => f.file_id === fileId)?.error_message && (
            <div className="error-box">
              {doc.files.find((f) => f.file_id === fileId)?.error_message}
            </div>
          )}
          {fileId ? (
            <div className="img-wrap">
              <img
                src={`/api/operator/files/${fileId}/pages/1`}
                alt="Оригинал документа"
              />
              {selected?.bbox &&
                selected.bbox.length === 4 &&
                (!selected.file_id || selected.file_id === fileId) && (
                  <div
                    className="bbox"
                    style={{
                      left: selected.bbox[0] / 10 + "%",
                      top: selected.bbox[1] / 10 + "%",
                      width: (selected.bbox[2] - selected.bbox[0]) / 10 + "%",
                      height: (selected.bbox[3] - selected.bbox[1]) / 10 + "%",
                    }}
                  />
                )}
            </div>
          ) : (
            <div className="muted">Нет файла для просмотра.</div>
          )}
        </div>

        <div className="fields">
          <div className="tabs">
            <button
              className={tab === "fields" ? "active" : ""}
              onClick={() => setTab("fields")}
            >
              Поля
            </button>
            <button
              className={tab === "history" ? "active" : ""}
              onClick={() => setTab("history")}
            >
              История
            </button>
          </div>
          {tab === "fields" && doc && (
            <>
              <div className="doc-actions">
                {canEdit && (
                  <button className="btn small" onClick={() => reprocess(doc)}>
                    Разобрать повторно
                  </button>
                )}
              </div>
              {doc.issues.length > 0 && (
                <IssueList issues={doc.issues} title="Замечания" />
              )}
              {fields.length === 0 && (
                <div className="muted">
                  Поля ещё не извлечены (документ: {statusRu(doc.status)}).
                </div>
              )}
              {fields.map((f) => (
                <FieldRow
                  key={f.id}
                  field={f}
                  active={f.id === fieldId}
                  canEdit={canEdit}
                  onSelect={() => selectField(f)}
                  onSaved={(msg) => {
                    setNotice(msg);
                    load();
                  }}
                />
              ))}
            </>
          )}
          {tab === "history" && <History id={a.id} />}
        </div>
      </div>
    </section>
  );
}

function IssueList({
  issues,
  title,
}: {
  issues: { severity: string; message: string }[];
  title: string;
}) {
  return (
    <div className="issues">
      <div className="issues-title">{title}</div>
      {issues.map((i, n) => (
        <div key={n} className={"issue sev-" + i.severity}>
          <b>{SEV_RU[i.severity] ?? i.severity}:</b> {i.message}
        </div>
      ))}
    </div>
  );
}

function FieldRow(props: {
  field: Field;
  active: boolean;
  canEdit: boolean;
  onSelect: () => void;
  onSaved: (msg: string) => void;
}) {
  const { field: f } = props;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(f.value ?? "");
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    try {
      await api.patch("/api/operator/fields/" + f.id, { value });
      setEditing(false);
      setErr(null);
      props.onSaved(`Поле «${f.label}» сохранено, правила перепроверены.`);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.status === 403 ? "Нет прав на правку." : ae.message);
    }
  }

  return (
    <div
      className={"field conf-" + f.confidence + (props.active ? " active" : "")}
      onClick={props.onSelect}
    >
      <div className="field-top">
        <span className="field-label">{f.label}</span>
        <span className="conf-tag">
          {CONF_RU[f.confidence] ?? f.confidence}
        </span>
      </div>
      {editing ? (
        <div className="field-edit" onClick={(e) => e.stopPropagation()}>
          <input
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <button className="btn small primary" onClick={save}>
            Сохранить
          </button>
          <button className="btn small" onClick={() => setEditing(false)}>
            Отмена
          </button>
        </div>
      ) : (
        <div className="field-value">
          {f.value ?? <span className="muted">— не прочитано —</span>}
          {props.canEdit && (
            <button
              className="btn tiny"
              onClick={(e) => {
                e.stopPropagation();
                setValue(f.value ?? "");
                setEditing(true);
              }}
            >
              Изменить
            </button>
          )}
        </div>
      )}
      <div className="field-src muted">
        {f.source === "operator"
          ? `правка оператора${f.edited_by ? ": " + f.edited_by : ""}`
          : "модель"}
      </div>
      {err && <div className="error-inline">{err}</div>}
    </div>
  );
}

function History({ id }: { id: string }) {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .get<{ items: HistoryItem[] }>(`/api/operator/applications/${id}/history`)
      .then((r) => setItems(r.items))
      .catch((e: ApiError) => setError(e.message));
  }, [id]);
  if (error) return <div className="error-box">{error}</div>;
  if (!items) return <div className="muted">Загрузка истории…</div>;
  if (items.length === 0) return <div className="muted">Событий пока нет.</div>;
  return (
    <ul className="history">
      {items.map((h, i) => (
        <li key={i}>
          <div className="muted">
            {fmtDate(h.at)} · {h.actor}
          </div>
          <div>{h.message}</div>
        </li>
      ))}
    </ul>
  );
}
