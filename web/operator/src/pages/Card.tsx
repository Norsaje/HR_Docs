// Карточка заявки — главный экран оператора. Три колонки: документы | оригинал | поля.
// Оригинал: зум колесом и кнопками, перетаскивание, поворот, переключение файлов документа;
// рамка выбранного или наведённого поля рисуется поверх изображения (bbox в долях 0–1000)
// и поворачивается вместе с ним. Поля — сначала красные, потом жёлтые, потом зелёные.
// Горячие клавиши: J/K — следующее/предыдущее поле, N — следующее красное или жёлтое,
// E — править, Enter — сохранить, Esc — отменить, V — отметить документ проверенным, ? — подсказка.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  canEdit,
  CONF_RANK,
  CONF_RU,
  errText,
  fmtDate,
  fmtDay,
  signalRu,
  STAGE_RU,
  type Card,
  type Doc,
  type Field,
  type HistoryItem,
  type Issue,
  type Me,
} from "../api";
import { Empty, ErrorBox, Skeleton, StatusBadge, useToast } from "../ui";

const SEV_RU: Record<string, string> = {
  error: "Ошибка",
  warning: "Внимание",
  info: "К сведению",
};

function sortFields(fields: Field[]): Field[] {
  return [...fields].sort(
    (a, b) => (CONF_RANK[a.confidence] ?? 9) - (CONF_RANK[b.confidence] ?? 9),
  );
}

// ---------------- Просмотрщик оригинала ----------------

function Viewer({
  fileId,
  fileIds,
  onFile,
  box,
  showOriginal,
  onToggleOriginal,
}: {
  fileId: string | null;
  fileIds: string[];
  onFile: (id: string) => void;
  box: number[] | null;
  showOriginal: boolean;
  onToggleOriginal: () => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [rot, setRot] = useState(0);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [usePreview, setUsePreview] = useState(true);
  const [failed, setFailed] = useState(false);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(
    null,
  );

  useEffect(() => {
    setZoom(1);
    setRot(0);
    setPan({ x: 0, y: 0 });
    setUsePreview(true);
    setFailed(false);
  }, [fileId]);

  if (!fileId)
    return (
      <Empty
        title="Нет файла"
        hint="Соискатель ещё не загрузил файлы этого документа."
      />
    );

  // По умолчанию — превью с размытым лицом; оригинал открывается по кнопке (просмотр пишется в журнал).
  const src =
    showOriginal || !usePreview
      ? `/api/operator/files/${fileId}/pages/1`
      : `/api/operator/files/${fileId}/preview`;

  const idx = fileIds.indexOf(fileId);
  return (
    <div className="viewer">
      <div className="viewer-tools">
        <button
          className="btn small ghost"
          onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
          title="Уменьшить"
        >
          −
        </button>
        <span className="mono small">{Math.round(zoom * 100)}%</span>
        <button
          className="btn small ghost"
          onClick={() => setZoom((z) => Math.min(5, z + 0.25))}
          title="Увеличить"
        >
          +
        </button>
        <button
          className="btn small ghost"
          onClick={() => setRot((r) => (r + 90) % 360)}
          title="Повернуть на 90°"
        >
          ⟳ Повернуть
        </button>
        <button
          className="btn small ghost"
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
            setRot(0);
          }}
        >
          Сбросить
        </button>
        <span className="grow" />
        {fileIds.length > 1 && (
          <span className="pager">
            <button
              className="btn small ghost"
              disabled={idx <= 0}
              onClick={() => onFile(fileIds[idx - 1])}
            >
              ‹
            </button>
            <span className="small">
              файл {idx + 1} из {fileIds.length}
            </span>
            <button
              className="btn small ghost"
              disabled={idx >= fileIds.length - 1}
              onClick={() => onFile(fileIds[idx + 1])}
            >
              ›
            </button>
          </span>
        )}
        <button
          className={`btn small ${showOriginal ? "" : "ghost"}`}
          onClick={onToggleOriginal}
        >
          {showOriginal ? "Скрыть оригинал" : "Показать оригинал"}
        </button>
      </div>
      <div
        className="viewer-stage"
        onWheel={(e) => {
          e.preventDefault();
          setZoom((z) =>
            Math.min(5, Math.max(0.5, z + (e.deltaY < 0 ? 0.15 : -0.15))),
          );
        }}
        onMouseDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
        }}
        onMouseMove={(e) => {
          if (!drag.current) return;
          setPan({
            x: drag.current.px + e.clientX - drag.current.x,
            y: drag.current.py + e.clientY - drag.current.y,
          });
        }}
        onMouseUp={() => (drag.current = null)}
        onMouseLeave={() => (drag.current = null)}
      >
        {failed ? (
          <div className="viewer-msg">
            Изображение недоступно: файл ещё разбирается или не прочитался.
          </div>
        ) : (
          <div
            className="viewer-canvas"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${rot}deg)`,
            }}
          >
            <img
              src={src}
              alt="Оригинал документа"
              draggable={false}
              onError={() => {
                if (usePreview && !showOriginal)
                  setUsePreview(false); // превью нет на сервере — берём страницу
                else setFailed(true);
              }}
            />
            {box && box.length === 4 && (
              <div
                className="bbox"
                style={{
                  left: `${box[0] / 10}%`,
                  top: `${box[1] / 10}%`,
                  width: `${Math.max(0.5, (box[2] - box[0]) / 10)}%`,
                  height: `${Math.max(0.5, (box[3] - box[1]) / 10)}%`,
                }}
              />
            )}
          </div>
        )}
      </div>
      <div className="viewer-foot muted small">
        {showOriginal || !usePreview
          ? "Показан оригинал — просмотр записан в журнал."
          : "Показано превью с размытым лицом. Колесо — масштаб, перетаскивание — сдвиг."}
      </div>
    </div>
  );
}

// ---------------- Строка поля ----------------

function FieldRow({
  f,
  selected,
  editable,
  editing,
  onSelect,
  onHover,
  onStartEdit,
  onCancel,
  onSave,
}: {
  f: Field;
  selected: boolean;
  editable: boolean;
  editing: boolean;
  onSelect: () => void;
  onHover: (on: boolean) => void;
  onStartEdit: () => void;
  onCancel: () => void;
  onSave: (v: string) => Promise<void>;
}) {
  const [val, setVal] = useState(f.value ?? "");
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (editing) {
      setVal(f.value ?? "");
      setTimeout(() => ref.current?.focus(), 0);
    }
  }, [editing, f.value]);
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const conf = f.confidence === "gray" ? "grey" : f.confidence;
  const signals = f.signals_ru?.length
    ? f.signals_ru
    : (f.signals ?? []).map(signalRu);

  async function save() {
    setSaving(true);
    try {
      await onSave(val);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      ref={rowRef}
      className={`field conf-${conf} ${selected ? "selected" : ""}`}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      data-testid={`field-${f.key}`}
    >
      <div className="field-head">
        <span className={`dot conf-${conf}`} />
        <span className="field-label">{f.label}</span>
        <span className={`conf-label conf-${conf}`}>
          {CONF_RU[conf] ?? conf}
        </span>
      </div>
      {editing ? (
        <div className="field-edit" onClick={(e) => e.stopPropagation()}>
          <input
            ref={ref}
            className="input grow"
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") onCancel();
            }}
            aria-label={`Новое значение: ${f.label}`}
          />
          <button
            className="btn small primary"
            disabled={saving}
            onClick={save}
          >
            Сохранить
          </button>
          <button className="btn small ghost" onClick={onCancel}>
            Отмена
          </button>
        </div>
      ) : (
        <div className="field-value-row">
          <span className={`field-value ${f.value ? "" : "muted"}`}>
            {f.value ??
              (f.status === "absent" ? "нет на документе" : "не прочитано")}
          </span>
          {editable && (
            <button
              className="btn small ghost edit-btn"
              onClick={(e) => {
                e.stopPropagation();
                onStartEdit();
              }}
              title="Править (E)"
            >
              Изменить
            </button>
          )}
        </div>
      )}
      {f.source === "operator" && (
        <div className="edited-badge">
          правка оператора: {f.edited_by ?? "—"}
          {f.edited_at ? `, ${fmtDate(f.edited_at)}` : ""}
        </div>
      )}
      {f.hint && <div className="field-hint">💡 {f.hint}</div>}
      {selected && (
        <div className="field-details">
          {signals.length > 0 && (
            <ul className="signals">
              {signals.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          )}
          {f.raw && f.raw !== f.value && (
            <div className="small">
              Как напечатано: <span className="mono">{f.raw}</span>
            </div>
          )}
          {f.candidates?.ocr && (
            <div className="small">
              OCR прочитал: <span className="mono">{f.candidates.ocr}</span>
            </div>
          )}
          {f.candidates?.mrz && (
            <div className="small">
              В машиночитаемой зоне:{" "}
              <span className="mono">{f.candidates.mrz}</span>
            </div>
          )}
          {f.candidates?.hints?.length ? (
            <div className="small">
              Варианты, при которых сходится контрольная сумма:{" "}
              {f.candidates.hints.map((h) => (
                <span key={h} className="mono cand">
                  {h}
                </span>
              ))}
            </div>
          ) : null}
          {!f.bbox && (
            <div className="muted small">Рамка поля не определена.</div>
          )}
        </div>
      )}
    </div>
  );
}

function IssueList({ items, title }: { items: Issue[]; title: string }) {
  if (!items.length) return null;
  return (
    <div className="issues">
      <div className="block-title">{title}</div>
      {items.map((i, n) => (
        <div key={n} className={`issue sev-${i.severity}`}>
          <b>{SEV_RU[i.severity] ?? i.severity}:</b> {i.message}
        </div>
      ))}
    </div>
  );
}

// ---------------- Запрос документов ----------------

function RequestDocs({
  card,
  onClose,
  onDone,
}: {
  card: Card;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const options =
    card.application.allowed_doc_types ??
    card.documents.map((d) => ({ doc_type: d.doc_type, title: d.title }));
  const missing = new Set(card.application.missing ?? []);
  const bad = new Set(
    card.documents
      .filter((d) =>
        ["unreadable", "failed", "needs_review"].includes(d.status),
      )
      .map((d) => d.doc_type),
  );
  const [sel, setSel] = useState<Set<string>>(new Set([...missing, ...bad]));
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    setBusy(true);
    try {
      await api.post(
        `/api/operator/applications/${card.application.id}/request-documents`,
        {
          doc_types: [...sel],
          comment,
        },
      );
      toast("ok", "Запрос отправлен соискателю на email");
      onDone();
    } catch (e) {
      toast("bad", errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-back" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Запросить документы"
      >
        <h3>Запросить документы у соискателя</h3>
        <div className="muted small">
          Письмо уйдёт на {card.application.email_masked}
        </div>
        <div className="checks">
          {options.map((o) => (
            <label key={o.doc_type} className="check">
              <input
                type="checkbox"
                checked={sel.has(o.doc_type)}
                onChange={(e) => {
                  const n = new Set(sel);
                  if (e.target.checked) n.add(o.doc_type);
                  else n.delete(o.doc_type);
                  setSel(n);
                }}
              />
              {o.title}
              {missing.has(o.doc_type) && (
                <span className="badge tone-bad">не загружен</span>
              )}
              {bad.has(o.doc_type) && (
                <span className="badge tone-warn">переснять</span>
              )}
            </label>
          ))}
        </div>
        <textarea
          className="input"
          rows={3}
          placeholder="Комментарий для соискателя: что именно переснять и как"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn primary"
            disabled={busy || sel.size === 0}
            onClick={send}
          >
            Отправить запрос
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------- Карточка ----------------

export function CardPage({ id, me }: { id: string; me: Me }) {
  const toast = useToast();
  const editable = canEdit(me);
  const [card, setCard] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"docs" | "history">("docs");
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [docId, setDocId] = useState<string | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [fieldId, setFieldId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [askDocs, setAskDocs] = useState(false);
  const [help, setHelp] = useState(false);
  const [moveTo, setMoveTo] = useState("");

  const load = useCallback(() => {
    api
      .get<Card>(`/api/operator/applications/${id}`)
      .then((c) => {
        setCard(c);
        setError(null);
        setDocId((cur) => cur ?? c.documents[0]?.id ?? null);
      })
      .catch((e) => setError(errText(e)));
  }, [id]);

  useEffect(load, [load]);
  useEffect(() => {
    if (tab === "history")
      api
        .get<{ items: HistoryItem[] }>(
          `/api/operator/applications/${id}/history`,
        )
        .then((r) => setHistory(r.items ?? []))
        .catch((e) => toast("bad", errText(e)));
  }, [tab, id, toast]);

  const doc: Doc | undefined = card?.documents.find((d) => d.id === docId);
  const fields = useMemo(() => sortFields(doc?.fields ?? []), [doc]);
  const fileIds = (doc?.files ?? []).map((f) => f.file_id);

  // При смене документа — первый файл и первое (самое красное) поле.
  useEffect(() => {
    if (!doc) return;
    setFileId((cur) =>
      cur && fileIds.includes(cur) ? cur : (fileIds[0] ?? null),
    );
    setFieldId((cur) =>
      cur && doc.fields.some((f) => f.id === cur) ? cur : null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, card]);

  const focusField = fields.find((f) => f.id === (hoverId ?? fieldId));
  // Рамку рисуем, только если поле прочитано с того же файла, что открыт.
  const box =
    focusField?.bbox && (!focusField.file_id || focusField.file_id === fileId)
      ? focusField.bbox
      : null;

  function selectField(f: Field) {
    setFieldId(f.id);
    if (f.file_id && f.file_id !== fileId && fileIds.includes(f.file_id))
      setFileId(f.file_id);
  }

  async function saveField(f: Field, value: string) {
    try {
      await api.patch(`/api/operator/fields/${f.id}`, { value });
      toast("ok", `Поле «${f.label}» сохранено, правила перепроверены`);
      setEditId(null);
      load();
    } catch (e) {
      toast(
        "bad",
        (e as { status?: number }).status === 403
          ? "Нет прав на правку"
          : errText(e),
      );
    }
  }

  async function act(url: string, okText: string, body?: unknown) {
    try {
      await api.post(url, body);
      toast("ok", okText);
      load();
    } catch (e) {
      toast("bad", errText(e));
    }
  }

  // Горячие клавиши (не срабатывают, пока курсор в поле ввода).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (
        t.tagName === "INPUT" ||
        t.tagName === "TEXTAREA" ||
        t.tagName === "SELECT" ||
        e.ctrlKey ||
        e.metaKey
      )
        return;
      if (!fields.length && e.key !== "?") return;
      const i = fields.findIndex((f) => f.id === fieldId);
      const k = e.key.toLowerCase();
      if (k === "j" || k === "о")
        selectField(fields[Math.min(fields.length - 1, i + 1)]);
      else if (k === "k" || k === "л") selectField(fields[Math.max(0, i - 1)]);
      else if (k === "n" || k === "т") {
        const rest = [...fields.slice(i + 1), ...fields.slice(0, i + 1)];
        const next = rest.find(
          (f) => f.confidence === "red" || f.confidence === "yellow",
        );
        if (next) selectField(next);
        else toast("info", "Красных и жёлтых полей больше нет");
      } else if ((k === "e" || k === "у") && editable && fieldId) {
        e.preventDefault();
        setEditId(fieldId);
      } else if ((k === "v" || k === "м") && editable && doc) {
        act(
          `/api/operator/documents/${doc.id}/verify`,
          `Документ «${doc.title}» отмечен проверенным`,
        );
      } else if (e.key === "?") setHelp((h) => !h);
      else if (e.key === "Escape") {
        setEditId(null);
        setHelp(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (error) return <ErrorBox text={error} onRetry={load} />;
  if (!card)
    return (
      <div className="card-grid">
        <Skeleton lines={5} height={44} />
        <Skeleton lines={1} height={420} />
        <Skeleton lines={8} height={52} />
      </div>
    );

  const a = card.application;
  const counts = fields.reduce<Record<string, number>>(
    (m, f) => ((m[f.confidence] = (m[f.confidence] ?? 0) + 1), m),
    {},
  );

  return (
    <section>
      <div className="page-head">
        <a href="#/" className="back">
          ← Заявки
        </a>
        <h1>
          <span className="mono">{a.public_no}</span> · {a.full_name}
        </h1>
        <StatusBadge status={a.status} />
        <span className="muted small">
          {a.country_code} · {a.email_masked} · {fmtDate(a.created_at)}
        </span>
        <span className="grow" />
        <a
          className="btn small ghost"
          href={`/api/operator/applications/${a.id}/export?format=json`}
        >
          Выгрузить JSON
        </a>
        <a
          className="btn small ghost"
          href={`/api/operator/applications/${a.id}/export?format=csv`}
        >
          Выгрузить CSV
        </a>
        {editable && (
          <button className="btn small" onClick={() => setAskDocs(true)}>
            Запросить документы
          </button>
        )}
        <button
          className="btn small ghost"
          onClick={() => setHelp(true)}
          title="Горячие клавиши (?)"
        >
          ?
        </button>
      </div>

      <div className="tabs">
        <button
          className={tab === "docs" ? "active" : ""}
          onClick={() => setTab("docs")}
        >
          Документы
        </button>
        <button
          className={tab === "history" ? "active" : ""}
          onClick={() => setTab("history")}
        >
          История
        </button>
      </div>

      {tab === "history" ? (
        <div className="history">
          {!history && <Skeleton lines={6} />}
          {history && history.length === 0 && (
            <Empty title="Событий пока нет" />
          )}
          {history && history.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>Кто</th>
                  <th>Что произошло</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={i}>
                    <td className="nowrap small">{fmtDate(h.at)}</td>
                    <td className="nowrap">{h.actor}</td>
                    <td>{h.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="card-grid">
          {/* ---- Левая колонка: документы ---- */}
          <aside className="col docs-col">
            <div className="block-title">Документы</div>
            {card.documents.length === 0 && (
              <Empty
                title="Документов нет"
                hint="Соискатель ещё ничего не загрузил."
              />
            )}
            {card.documents.map((d) => {
              const red = d.fields.filter((f) => f.confidence === "red").length;
              return (
                <button
                  key={d.id}
                  className={`doc-item ${d.id === docId ? "active" : ""}`}
                  onClick={() => {
                    setDocId(d.id);
                    setFieldId(null);
                    setEditId(null);
                  }}
                >
                  <div className="doc-title">{d.title}</div>
                  <div className="doc-meta">
                    <StatusBadge status={d.status} />
                    {red > 0 && <span className="err-count">{red} красн.</span>}
                    {d.verified_by && (
                      <span className="badge tone-ok">✓ проверено</span>
                    )}
                  </div>
                  {d.status === "processing" && d.stage && (
                    <div className="muted small">
                      этап: {STAGE_RU[d.stage] ?? d.stage}
                    </div>
                  )}
                  {d.expires_on && (
                    <div className="muted small">
                      действует до {fmtDay(d.expires_on)}
                    </div>
                  )}
                </button>
              );
            })}
            {(a.missing ?? []).length > 0 && (
              <div className="missing">
                <div className="block-title">Не загружены</div>
                {(a.missing ?? []).map((m) => (
                  <div key={m} className="badge tone-bad">
                    {a.allowed_doc_types?.find((x) => x.doc_type === m)
                      ?.title ?? m}
                  </div>
                ))}
              </div>
            )}
            <IssueList
              items={[...(card.crosscheck ?? []), ...(card.issues ?? [])]}
              title="Сверка между документами"
            />
          </aside>

          {/* ---- Центр: оригинал ---- */}
          <div className="col viewer-col">
            <Viewer
              fileId={fileId}
              fileIds={fileIds}
              onFile={setFileId}
              box={box}
              showOriginal={showOriginal}
              onToggleOriginal={() => setShowOriginal((s) => !s)}
            />
            {doc?.files.find((f) => f.file_id === fileId)?.error_message && (
              <div className="issue sev-error">
                {doc.files.find((f) => f.file_id === fileId)?.error_message}
              </div>
            )}
          </div>

          {/* ---- Правая колонка: поля ---- */}
          <div className="col fields-col">
            {doc ? (
              <>
                <div className="fields-head">
                  <div className="block-title">{doc.title}</div>
                  <div className="conf-summary">
                    {(["red", "yellow", "green"] as const).map((c) =>
                      counts[c] ? (
                        <span key={c} className={`conf-pill conf-${c}`}>
                          {counts[c]} {CONF_RU[c]}
                        </span>
                      ) : null,
                    )}
                  </div>
                </div>
                {editable && (
                  <div className="doc-actions">
                    <button
                      className="btn small"
                      onClick={() =>
                        act(
                          `/api/operator/documents/${doc.id}/reprocess`,
                          "Документ поставлен на повторный разбор",
                        )
                      }
                    >
                      Разобрать повторно
                    </button>
                    <button
                      className="btn small primary"
                      disabled={!!doc.verified_by}
                      onClick={() =>
                        act(
                          `/api/operator/documents/${doc.id}/verify`,
                          `Документ «${doc.title}» отмечен проверенным`,
                        )
                      }
                      title="Отметить проверенным (V)"
                    >
                      {doc.verified_by
                        ? `Проверено: ${doc.verified_by}`
                        : "Отметить проверенным"}
                    </button>
                    {fileId && (a.allowed_doc_types?.length ?? 0) > 1 && (
                      <span className="move">
                        <select
                          className="input small"
                          value={moveTo}
                          onChange={(e) => setMoveTo(e.target.value)}
                          aria-label="Перенести файл"
                        >
                          <option value="">Перенести файл в…</option>
                          {a.allowed_doc_types
                            ?.filter((x) => x.doc_type !== doc.doc_type)
                            .map((x) => (
                              <option key={x.doc_type} value={x.doc_type}>
                                {x.title}
                              </option>
                            ))}
                        </select>
                        {moveTo && (
                          <button
                            className="btn small"
                            onClick={() => {
                              act(
                                `/api/operator/files/${fileId}/move`,
                                "Файл перенесён и будет разобран заново",
                                { doc_type: moveTo },
                              );
                              setMoveTo("");
                            }}
                          >
                            Перенести
                          </button>
                        )}
                      </span>
                    )}
                  </div>
                )}
                {doc.message && (
                  <div className="issue sev-warning">{doc.message}</div>
                )}
                <IssueList
                  items={doc.issues ?? []}
                  title="Замечания по документу"
                />
                {fields.length === 0 ? (
                  <Empty
                    title={
                      doc.status === "processing" || doc.status === "uploaded"
                        ? "Документ ещё разбирается"
                        : "Полей нет"
                    }
                    hint={
                      doc.status === "processing" || doc.status === "uploaded"
                        ? "Поля появятся, когда модель закончит чтение."
                        : "Документ не прочитался — запросите новое фото или запустите разбор повторно."
                    }
                  />
                ) : (
                  <div className="fields">
                    {fields.map((f) => (
                      <FieldRow
                        key={f.id}
                        f={f}
                        selected={f.id === fieldId}
                        editable={editable}
                        editing={f.id === editId}
                        onSelect={() => selectField(f)}
                        onHover={(on) => setHoverId(on ? f.id : null)}
                        onStartEdit={() => {
                          selectField(f);
                          setEditId(f.id);
                        }}
                        onCancel={() => setEditId(null)}
                        onSave={(v) => saveField(f, v)}
                      />
                    ))}
                  </div>
                )}
                {!editable && (
                  <div className="muted small">
                    У вас роль «просмотр»: правка недоступна.
                  </div>
                )}
              </>
            ) : (
              <Empty title="Выберите документ слева" />
            )}
          </div>
        </div>
      )}

      {askDocs && (
        <RequestDocs
          card={card}
          onClose={() => setAskDocs(false)}
          onDone={() => {
            setAskDocs(false);
            load();
          }}
        />
      )}
      {help && (
        <div className="modal-back" onClick={() => setHelp(false)}>
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="Горячие клавиши"
          >
            <h3>Горячие клавиши</h3>
            <table className="keys">
              <tbody>
                <tr>
                  <td>
                    <kbd>J</kbd> / <kbd>K</kbd>
                  </td>
                  <td>следующее / предыдущее поле</td>
                </tr>
                <tr>
                  <td>
                    <kbd>N</kbd>
                  </td>
                  <td>следующее красное или жёлтое поле</td>
                </tr>
                <tr>
                  <td>
                    <kbd>E</kbd>
                  </td>
                  <td>править выбранное поле</td>
                </tr>
                <tr>
                  <td>
                    <kbd>Enter</kbd> / <kbd>Esc</kbd>
                  </td>
                  <td>сохранить / отменить правку</td>
                </tr>
                <tr>
                  <td>
                    <kbd>V</kbd>
                  </td>
                  <td>отметить документ проверенным</td>
                </tr>
                <tr>
                  <td>
                    <kbd>?</kbd>
                  </td>
                  <td>эта подсказка</td>
                </tr>
              </tbody>
            </table>
            <div className="modal-actions">
              <button className="btn primary" onClick={() => setHelp(false)}>
                Понятно
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
