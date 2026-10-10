// Страница соискателя: анкета с согласием → загрузка документов → отправка → статусы без перезагрузки.
// Опрос состояния заявки — каждые 2 секунды. Значений полей соискатель не видит, только статусы.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiError,
  clearSession,
  loadSession,
  saveSession,
  type AppDocument,
  type AppState,
  type Country,
  type FileResult,
  type Requirement,
  type Session,
} from "./api";

const MAX_MB = 20;

const STATUS_RU: Record<string, string> = {
  missing: "Не загружен",
  uploaded: "Принят, ждёт разбора",
  processing: "Разбирается",
  ready: "Готово",
  needs_review: "Есть замечания",
  unreadable: "Не прочитался — загрузите снова",
  failed: "Ошибка разбора — повторим автоматически",
};

const STAGE_RU: Record<string, string> = {
  queued: "в очереди",
  preprocess: "подготовка изображения",
  extract: "чтение документа",
  verify: "проверка данных",
  done: "завершено",
};

const FILE_STATUS_RU: Record<string, string> = {
  queued: "ждёт разбора",
  processing: "разбирается",
  done: "разобран",
  ready: "разобран",
  unreadable: "не прочитался",
  failed: "ошибка разбора",
  rejected: "отклонён",
};

function statusText(doc: AppDocument): string {
  const base = STATUS_RU[doc.status] ?? doc.status;
  if (doc.status === "processing" && doc.stage)
    return `${base} (${STAGE_RU[doc.stage] ?? doc.stage})`;
  return base;
}

function statusClass(status: string): string {
  if (status === "ready") return "ok";
  if (status === "needs_review") return "warn";
  if (status === "unreadable" || status === "failed") return "bad";
  if (status === "missing") return "muted";
  return "info";
}

export default function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [country, setCountry] = useState<string>(() => {
    try {
      return localStorage.getItem("hr_country") ?? "";
    } catch {
      return "";
    }
  });

  const onCreated = (s: Session, countryCode: string) => {
    saveSession(s);
    try {
      localStorage.setItem("hr_country", countryCode);
    } catch {
      /* ignore */
    }
    setCountry(countryCode);
    setSession(s);
  };

  const onReset = () => {
    clearSession();
    setSession(null);
  };

  return (
    <div className="page">
      <header className="top">
        <h1>Приём документов</h1>
        <p className="sub">
          Загрузите документы для оформления. Ссылку на заявку мы пришлём на
          email.
        </p>
      </header>
      {session ? (
        <ApplicationScreen
          session={session}
          country={country}
          onReset={onReset}
        />
      ) : (
        <StartScreen onCreated={onCreated} />
      )}
      <footer className="foot">
        <a href="/legal/consent-v1.html" target="_blank" rel="noreferrer">
          Согласие на обработку персональных данных
        </a>
        {" · "}
        <a href="/legal/policy-v1.html" target="_blank" rel="noreferrer">
          Политика обработки персональных данных
        </a>
      </footer>
    </div>
  );
}

// ---------------- Экран 1: анкета и согласие ----------------

function StartScreen({
  onCreated,
}: {
  onCreated: (s: Session, country: string) => void;
}) {
  const [countries, setCountries] = useState<Country[]>([]);
  const [country, setCountry] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [requirements, setRequirements] = useState<Requirement[]>([]);

  useEffect(() => {
    api
      .countries()
      .then(setCountries)
      .catch((e: ApiError) => setLoadError(e.message));
  }, []);

  useEffect(() => {
    if (!country) {
      setRequirements([]);
      return;
    }
    api
      .requirements(country)
      .then((r) => setRequirements(r.documents))
      .catch(() => setRequirements([]));
  }, [country]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const s = await api.createApplication({
        country_code: country,
        full_name: fullName.trim(),
        email: email.trim(),
        consent_accepted: consent,
      });
      onCreated(s, country);
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h2>Шаг 1. О вас</h2>
      {loadError && <p className="alert bad">{loadError}</p>}
      <label className="field">
        <span>Гражданство</span>
        <select
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          required
        >
          <option value="">Выберите страну</option>
          {countries.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name_ru}
            </option>
          ))}
        </select>
      </label>
      {requirements.length > 0 && (
        <div className="hint-box">
          <b>Понадобятся документы:</b>
          <ul>
            {requirements.map((r) => (
              <li key={r.doc_type}>
                {r.title}
                {!r.required && <span className="muted"> (необязательно)</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <label className="field">
        <span>Фамилия, имя, отчество</span>
        <input
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          autoComplete="name"
          required
          maxLength={200}
          placeholder="Как в паспорте"
        />
      </label>
      <label className="field">
        <span>Email для связи</span>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
          maxLength={200}
          placeholder="name@example.com"
        />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
        />
        <span>
          Я даю{" "}
          <a href="/legal/consent-v1.html" target="_blank" rel="noreferrer">
            согласие на обработку персональных данных
          </a>{" "}
          и ознакомлен(а) с{" "}
          <a href="/legal/policy-v1.html" target="_blank" rel="noreferrer">
            политикой обработки персональных данных
          </a>
        </span>
      </label>
      {error && <p className="alert bad">{error}</p>}
      <button className="primary" type="submit" disabled={busy || !country}>
        {busy ? "Создаём заявку…" : "Продолжить"}
      </button>
    </form>
  );
}

// ---------------- Экраны 2–4: загрузка, отправка, статусы ----------------

function ApplicationScreen({
  session,
  country,
  onReset,
}: {
  session: Session;
  country: string;
  onReset: () => void;
}) {
  const [state, setState] = useState<AppState | null>(null);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [pollError, setPollError] = useState("");
  const [fatal, setFatal] = useState("");
  const [submitted, setSubmitted] = useState<{
    public_no: string;
    missing: string[];
  } | null>(null);
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<Record<string, FileResult[]>>({});

  const refresh = useCallback(async () => {
    try {
      const s = await api.state(session);
      setState(s);
      setPollError("");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 401 || err.status === 404) setFatal(err.message);
      else setPollError(err.message);
    }
  }, [session]);

  // Опрос состояния каждые 2 с — статусы обновляются без перезагрузки страницы.
  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (!country) return;
    api
      .requirements(country)
      .then((r) => setRequirements(r.documents))
      .catch(() => setRequirements([]));
  }, [country]);

  const reqByType = useMemo(
    () => Object.fromEntries(requirements.map((r) => [r.doc_type, r])),
    [requirements],
  );
  const titleOf = (docType: string) =>
    reqByType[docType]?.title ??
    state?.documents.find((d) => d.doc_type === docType)?.title ??
    docType;

  // Список документов: из состояния заявки (там есть статусы), а если его ещё нет — из требований страны.
  const documents: AppDocument[] = state?.documents.length
    ? state.documents
    : requirements.map((r) => ({
        doc_type: r.doc_type,
        title: r.title,
        status: "missing",
        files: [],
      }));

  const isSubmitted =
    !!submitted || (state ? !["draft", "new"].includes(state.status) : false);
  const publicNo =
    state?.public_no || submitted?.public_no || session.public_no;

  const upload = async (docType: string, files: FileList) => {
    for (const file of Array.from(files)) {
      let result: FileResult;
      if (file.size > MAX_MB * 1024 * 1024) {
        // Подсказка до отправки; окончательно размер всё равно проверяет сервер.
        result = {
          name: file.name,
          accepted: false,
          error: { code: "TOO_BIG", message: `Файл больше ${MAX_MB} МБ` },
        };
      } else {
        setResults((r) => ({
          ...r,
          [docType]: [
            ...(r[docType] ?? []),
            { name: file.name, accepted: false },
          ],
        }));
        result = await api.uploadFile(session, docType, file);
        setResults((r) => ({
          ...r,
          [docType]: (r[docType] ?? []).filter(
            (x) => !(x.name === file.name && !x.error && !x.accepted),
          ),
        }));
      }
      setResults((r) => ({ ...r, [docType]: [...(r[docType] ?? []), result] }));
    }
    void refresh();
  };

  const submit = async () => {
    setSubmitError("");
    setSubmitting(true);
    try {
      const r = await api.submit(session);
      setSubmitted(r);
      void refresh();
    } catch (e) {
      setSubmitError((e as ApiError).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (fatal) {
    return (
      <div className="card">
        <p className="alert bad">{fatal}</p>
        <button className="secondary" onClick={onReset}>
          Начать новую заявку
        </button>
      </div>
    );
  }

  const missing = state?.missing ?? submitted?.missing ?? [];
  const unreadable = documents.filter((d) => d.status === "unreadable");

  return (
    <>
      {isSubmitted && (
        <div className="card confirm">
          <h2>Комплект отправлен</h2>
          <p>
            Номер заявки: <b className="no">{publicNo}</b>
          </p>
          <p className="muted">
            Документы разбираются автоматически, статусы ниже обновляются сами.
            Сохраните эту страницу в закладки — по ней можно дозагрузить
            документы в ту же заявку.
          </p>
        </div>
      )}
      {!isSubmitted && publicNo && (
        <p className="muted small">
          Заявка № <b>{publicNo}</b>. Загрузите документы и нажмите «Отправить
          комплект».
        </p>
      )}

      {(missing.length > 0 || unreadable.length > 0) && (
        <div className="card alert-card">
          {missing.length > 0 && (
            <p>
              <b>Не хватает:</b> {missing.map(titleOf).join(", ")}
            </p>
          )}
          {unreadable.map((d) => (
            <p key={d.doc_type}>
              <b>Не прочитался «{d.title}»:</b>{" "}
              {d.message || "плохое качество снимка"}. Загрузите снова.
            </p>
          ))}
        </div>
      )}
      {pollError && <p className="alert warn">{pollError}</p>}

      <div className="docs">
        {documents.map((doc) => (
          <DocumentCard
            key={doc.doc_type}
            doc={doc}
            req={reqByType[doc.doc_type]}
            statusText={statusText(doc)}
            statusClass={statusClass(doc.status)}
            results={results[doc.doc_type] ?? []}
            onFiles={(files) => void upload(doc.doc_type, files)}
            isSubmitted={isSubmitted}
          />
        ))}
      </div>

      {!isSubmitted && (
        <div className="card">
          {submitError && <p className="alert bad">{submitError}</p>}
          <button
            className="primary"
            onClick={() => void submit()}
            disabled={submitting}
          >
            {submitting ? "Отправляем…" : "Отправить комплект"}
          </button>
        </div>
      )}
      <p className="center">
        <button className="link" onClick={onReset}>
          Начать новую заявку
        </button>
      </p>
    </>
  );
}

function DocumentCard({
  doc,
  req,
  statusText,
  statusClass,
  results,
  onFiles,
  isSubmitted,
}: {
  doc: AppDocument;
  req?: Requirement;
  statusText: string;
  statusClass: string;
  results: FileResult[];
  onFiles: (files: FileList) => void;
  isSubmitted: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const hasFiles = doc.files.length > 0;
  return (
    <div className="card doc">
      <div className="doc-head">
        <h3>
          {doc.title}
          {req && !req.required && (
            <span className="muted small"> · необязательно</span>
          )}
        </h3>
        <span className={`badge ${statusClass}`}>{statusText}</span>
      </div>
      {req?.hint && <p className="hint">{req.hint}</p>}
      {doc.message && <p className={`msg ${statusClass}`}>{doc.message}</p>}

      {hasFiles && (
        <ul className="files">
          {doc.files.map((f) => (
            <li key={f.file_id}>
              <span className="fname">{f.name}</span>
              <span className="muted">
                {" "}
                — {FILE_STATUS_RU[f.status] ?? f.status}
              </span>
              {f.error_message && <div className="ferr">{f.error_message}</div>}
            </li>
          ))}
        </ul>
      )}

      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => (
            <li
              key={i}
              className={r.accepted ? "ok" : r.error ? "bad" : "info"}
            >
              <span className="fname">{r.name}</span>
              {" — "}
              {r.accepted
                ? "принят"
                : r.error
                  ? `отклонён: ${r.error.message}`
                  : "загружается…"}
            </li>
          ))}
        </ul>
      )}

      <input
        ref={input}
        type="file"
        multiple
        accept="image/*,application/pdf"
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <button className="secondary" onClick={() => input.current?.click()}>
        {hasFiles || isSubmitted
          ? "Дозагрузить файл"
          : "Сфотографировать или выбрать файл"}
      </button>
      {req && (
        <p className="muted small">
          До {req.max_files} файлов, JPG, PNG, HEIC или PDF, до {MAX_MB} МБ.
        </p>
      )}
    </div>
  );
}
