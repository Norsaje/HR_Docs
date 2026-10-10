// Страница соискателя: «1 Кто вы → 2 Документы → 3 Статус».
// Анкета с согласием → загрузка документов (камера, галерея, перетаскивание) → отправка → статусы без
// перезагрузки (опрос каждые 2 с). Значений полей соискатель не видит, только статусы и безопасные тексты.
// Все проверки — на сервере; здесь только подсказки (размер файла до отправки).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiError,
  clearSession,
  linkFor,
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
const POLL_MS = 2000;

// Стадии разбора → «этап N из 4».
const STAGES: Record<string, { n: number; text: string }> = {
  queued: { n: 1, text: "В очереди" },
  preprocess: { n: 2, text: "Подготовка изображения" },
  extract: { n: 3, text: "Читаем документ" },
  verify: { n: 4, text: "Проверяем данные" },
};

type Tone = "ok" | "warn" | "bad" | "info" | "muted";

function docView(doc: AppDocument): {
  tone: Tone;
  title: string;
  detail?: string;
  stage?: number;
} {
  switch (doc.status) {
    case "ready":
      return {
        tone: "ok",
        title: "Готово",
        detail: "Документ прочитан и проверен",
      };
    case "needs_review":
      return {
        tone: "warn",
        title: "Принят, есть замечания",
        detail: "Оператор проверит документ",
      };
    case "unreadable":
      return {
        tone: "bad",
        title: "Не прочитался",
        detail: "Загрузите более чёткое фото",
      };
    case "failed":
      return {
        tone: "bad",
        title: "Ошибка разбора",
        detail:
          "Сервис распознавания временно недоступен. Мы повторим автоматически",
      };
    case "missing":
      return { tone: "muted", title: "Не загружен" };
    case "uploaded":
    case "processing": {
      const st = STAGES[doc.stage ?? "queued"] ?? STAGES.queued;
      const label = doc.stage_ru || st.text;
      return {
        tone: "info",
        title: label,
        detail: `этап ${st.n} из 4`,
        stage: st.n,
      };
    }
    default:
      return { tone: "info", title: doc.status_ru ?? "Обрабатывается" };
  }
}

const FILE_STATUS_RU: Record<string, string> = {
  queued: "ждёт разбора",
  processing: "читаем",
  done: "прочитан",
  unreadable: "не прочитался",
  failed: "ошибка разбора",
};

// ---------- маленькие иконки (inline SVG, без внешних ресурсов) ----------

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const p = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "ok":
      return (
        <svg {...p}>
          <path d="M20 6 9 17l-5-5" />
        </svg>
      );
    case "bad":
      return (
        <svg {...p}>
          <circle cx="12" cy="12" r="9" />
          <path d="m15 9-6 6M9 9l6 6" />
        </svg>
      );
    case "warn":
      return (
        <svg {...p}>
          <path d="M12 3 2 21h20L12 3z" />
          <path d="M12 10v4M12 17.5v.5" />
        </svg>
      );
    case "info":
      return (
        <svg {...p} className="spin">
          <path d="M21 12a9 9 0 1 1-6.2-8.6" />
        </svg>
      );
    case "camera":
      return (
        <svg {...p}>
          <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
          <circle cx="12" cy="13" r="3.5" />
        </svg>
      );
    case "file":
      return (
        <svg {...p}>
          <path d="M14 3H6v18h12V7z" />
          <path d="M14 3v4h4" />
        </svg>
      );
    case "copy":
      return (
        <svg {...p}>
          <rect x="8" y="8" width="12" height="12" rx="2" />
          <path d="M16 8V4H4v12h4" />
        </svg>
      );
    case "search":
      return (
        <svg {...p}>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
      );
    default:
      return (
        <svg {...p}>
          <circle cx="12" cy="12" r="9" />
        </svg>
      );
  }
}

// ---------- приложение ----------

export default function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [submitted, setSubmitted] = useState(false);
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
    window.scrollTo({ top: 0 });
  };

  const onReset = () => {
    if (
      !window.confirm(
        "Начать новую заявку? Ссылку на текущую сохраните заранее.",
      )
    )
      return;
    clearSession();
    setSubmitted(false);
    setSession(null);
  };

  const step = !session ? 1 : submitted ? 3 : 2;

  return (
    <div className="page">
      <header className="top">
        <div className="brand">
          <span className="logo" aria-hidden>
            HR
          </span>
          <div>
            <h1>Приём документов</h1>
            <p className="sub">
              Загрузите документы с телефона — мы проверим их и сообщим
              результат
            </p>
          </div>
        </div>
        <Steps step={step} />
      </header>
      {session ? (
        <EmailGate session={session}>
          <ApplicationScreen
            session={session}
            country={country}
            onReset={onReset}
            onSubmittedChange={setSubmitted}
          />
        </EmailGate>
      ) : (
        <StartScreen onCreated={onCreated} />
      )}
      <footer className="foot">
        <a href="/legal/consent-v1.html" target="_blank" rel="noreferrer">
          Согласие на обработку персональных данных
        </a>
        <a href="/legal/policy-v1.html" target="_blank" rel="noreferrer">
          Политика обработки персональных данных
        </a>
      </footer>
    </div>
  );
}

// Шаг «подтвердите email»: пока код из письма не введён, сервер не принимает файлы (403),
// поэтому и экран документов не показываем.
function EmailGate({
  session,
  children,
}: {
  session: Session;
  children: React.ReactNode;
}) {
  const [confirmed, setConfirmed] = useState<boolean | null>(null);
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .state(session)
      .then((st) => setConfirmed(st.email_confirmed !== false))
      .catch(() => setConfirmed(true)); // ошибку покажет экран заявки
  }, [session]);

  if (confirmed === null) return <div className="card">Загружаем заявку…</div>;
  if (confirmed) return <>{children}</>;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api.confirmEmail(session, code);
      setConfirmed(true);
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const resend = async () => {
    setMsg(null);
    try {
      await api.resendCode(session);
      setMsg("Новый код отправлен — проверьте почту.");
    } catch (err) {
      setMsg((err as Error).message);
    }
  };

  return (
    <form className="card" onSubmit={submit} data-testid="email-gate">
      <h2>Подтвердите email</h2>
      <p className="muted">
        Мы отправили письмо с кодом из 6 цифр. Введите его, чтобы перейти к
        загрузке документов (заявка {session.public_no}).
      </p>
      <label className="field">
        <span className="label">Код из письма</span>
        <input
          value={code}
          onChange={(e) =>
            setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
          }
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="000000"
          required
          minLength={6}
          maxLength={6}
        />
      </label>
      {msg && (
        <p className="muted" role="alert">
          {msg}
        </p>
      )}
      <button
        className="primary"
        type="submit"
        disabled={busy || code.length !== 6}
      >
        Подтвердить
      </button>
      <button type="button" className="link-btn" onClick={() => void resend()}>
        Отправить код ещё раз
      </button>
    </form>
  );
}

function Steps({ step }: { step: number }) {
  const items = ["Кто вы", "Документы", "Статус"];
  return (
    <ol className="steps" aria-label="Шаги">
      {items.map((t, i) => {
        const n = i + 1;
        const cls = n < step ? "done" : n === step ? "current" : "";
        return (
          <li
            key={t}
            className={cls}
            aria-current={n === step ? "step" : undefined}
          >
            <span className="num">
              {n < step ? <Icon name="ok" size={14} /> : n}
            </span>
            <span className="lbl">{t}</span>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------- Шаг 1: анкета и согласие ----------------

function StartScreen({
  onCreated,
}: {
  onCreated: (s: Session, country: string) => void;
}) {
  const [countries, setCountries] = useState<Country[]>([]);
  const [country, setCountry] = useState("");
  const [query, setQuery] = useState("");
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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q
      ? countries.filter((c) => c.name_ru.toLowerCase().includes(q))
      : countries;
  }, [countries, query]);
  const chosen = countries.find((c) => c.code === country);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!country) return setError("Выберите страну гражданства.");
    if (!consent)
      return setError(
        "Чтобы продолжить, отметьте согласие на обработку персональных данных.",
      );
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
    <form className="card" onSubmit={submit} noValidate={false}>
      <h2>Кто вы</h2>
      {loadError && <p className="alert bad">{loadError}</p>}

      <div className="field">
        <span className="label">Гражданство</span>
        {chosen ? (
          <div className="chosen">
            <b>{chosen.name_ru}</b>
            <button
              type="button"
              className="link"
              onClick={() => setCountry("")}
            >
              Изменить
            </button>
          </div>
        ) : (
          <>
            <div className="search">
              <Icon name="search" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Найти страну"
                aria-label="Поиск страны"
              />
            </div>
            <div className="countries" role="listbox" aria-label="Страны">
              {filtered.map((c) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  key={c.code}
                  className="country"
                  onClick={() => {
                    setCountry(c.code);
                    setQuery("");
                  }}
                >
                  {c.name_ru}
                </button>
              ))}
              {filtered.length === 0 && (
                <p className="muted small pad">Страна не найдена</p>
              )}
            </div>
          </>
        )}
      </div>

      {requirements.length > 0 && (
        <div className="hint-box">
          <b>Понадобятся документы:</b>
          <ul>
            {requirements.map((r) => (
              <li key={r.doc_type}>
                {r.title}
                {!r.required && <span className="muted"> — необязательно</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <label className="field">
        <span className="label">Фамилия, имя, отчество</span>
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
        <span className="label">Email для связи</span>
        <input
          type="email"
          inputMode="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
          maxLength={200}
          placeholder="name@example.com"
        />
        <span className="muted small">
          Пришлём ссылку на заявку и сообщим, если чего-то не хватит
        </span>
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
      {error && (
        <p className="alert bad" role="alert">
          {error}
        </p>
      )}
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Создаём заявку…" : "Продолжить"}
      </button>
    </form>
  );
}

// ---------------- Шаги 2–3: загрузка, отправка, статусы ----------------

type Upload = {
  id: number;
  name: string;
  thumb?: string;
  progress: number;
  result?: FileResult;
};

let uploadSeq = 0;

function ApplicationScreen({
  session,
  country,
  onReset,
  onSubmittedChange,
}: {
  session: Session;
  country: string;
  onReset: () => void;
  onSubmittedChange: (v: boolean) => void;
}) {
  const [state, setState] = useState<AppState | null>(null);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [pollError, setPollError] = useState("");
  const [fatal, setFatal] = useState("");
  const [submitResult, setSubmitResult] = useState<{
    public_no: string;
    missing: string[];
  } | null>(null);
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploads, setUploads] = useState<Record<string, Upload[]>>({});

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
    const t = window.setInterval(() => void refresh(), POLL_MS);
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
    state?.documents.find((d) => d.doc_type === docType)?.title ??
    reqByType[docType]?.title ??
    docType;

  const documents: AppDocument[] = state?.documents.length
    ? state.documents
    : requirements.map((r) => ({
        doc_type: r.doc_type,
        title: r.title,
        status: "missing",
        files: [],
      }));

  const isSubmitted =
    !!submitResult ||
    (state ? !["draft", "new"].includes(state.status) : false);
  useEffect(
    () => onSubmittedChange(isSubmitted),
    [isSubmitted, onSubmittedChange],
  );
  const publicNo =
    state?.public_no || submitResult?.public_no || session.public_no;

  const patchUpload = (docType: string, id: number, patch: Partial<Upload>) =>
    setUploads((u) => ({
      ...u,
      [docType]: (u[docType] ?? []).map((x) =>
        x.id === id ? { ...x, ...patch } : x,
      ),
    }));

  const upload = async (docType: string, files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      const id = ++uploadSeq;
      const thumb = file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined;
      setUploads((u) => ({
        ...u,
        [docType]: [
          ...(u[docType] ?? []),
          { id, name: file.name, thumb, progress: 0 },
        ],
      }));
      let result: FileResult;
      if (file.size > MAX_MB * 1024 * 1024) {
        // Подсказка до отправки; окончательно размер всё равно проверяет сервер.
        result = {
          name: file.name,
          accepted: false,
          error: {
            code: "TOO_BIG",
            message: `Файл больше ${MAX_MB} МБ. Сожмите фото или сделайте новый снимок.`,
          },
        };
      } else {
        result = await api.uploadFile(session, docType, file, (p) =>
          patchUpload(docType, id, { progress: p }),
        );
      }
      patchUpload(docType, id, { progress: 1, result });
    }
    void refresh();
  };

  const submit = async () => {
    setSubmitError("");
    setSubmitting(true);
    try {
      const r = await api.submit(session);
      setSubmitResult(r);
      void refresh();
      window.scrollTo({ top: 0, behavior: "smooth" });
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

  const missing = state?.missing ?? submitResult?.missing ?? [];
  const unreadable = documents.filter((d) => d.status === "unreadable");
  const uploadedCount = documents.filter((d) => d.files.length > 0).length;
  // Прогресс считаем по обязательным документам и тем необязательным, что соискатель загрузил.
  const counted = documents.filter(
    (d) => d.required !== false || d.files.length > 0,
  );
  const doneCount = counted.filter((d) =>
    ["ready", "needs_review"].includes(d.status),
  ).length;

  return (
    <>
      <NumberCard
        session={session}
        publicNo={publicNo}
        submitted={isSubmitted}
      />

      {isSubmitted && counted.length > 0 && (
        <div className="progress-card card">
          <div className="row">
            <b>
              Проверено документов: {doneCount} из {counted.length}
            </b>
          </div>
          <div className="bar">
            <span
              style={{
                width: `${counted.length ? (doneCount / counted.length) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {state && (
        <p className="muted" data-testid="email-status">
          {state.email_confirmed
            ? "✓ Email подтверждён — уведомления будут приходить на него."
            : "Мы отправили письмо со ссылкой для подтверждения email — откройте его и перейдите по ссылке."}
        </p>
      )}
      {(missing.length > 0 || unreadable.length > 0) && (
        <div className="card alert-card" role="status">
          <h3>{isSubmitted ? "Нужно дослать" : "Осталось загрузить"}</h3>
          {missing.map((dt) => (
            <div className="need" key={dt}>
              <span>
                <b>Не хватает:</b> {titleOf(dt)}
              </span>
              <button
                className="small-btn"
                onClick={() => document.getElementById(`pick-${dt}`)?.click()}
              >
                Загрузить
              </button>
            </div>
          ))}
          {unreadable.map((d) => (
            <div className="need" key={d.doc_type}>
              <span>
                <b>Не прочитался «{d.title}»:</b>{" "}
                {d.message || "плохое качество снимка"}. Загрузите снова.
              </span>
              <button
                className="small-btn"
                onClick={() =>
                  document.getElementById(`cam-${d.doc_type}`)?.click()
                }
              >
                Переснять
              </button>
            </div>
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
            uploads={uploads[doc.doc_type] ?? []}
            onFiles={(files) => void upload(doc.doc_type, files)}
            isSubmitted={isSubmitted}
          />
        ))}
      </div>

      {!isSubmitted && (
        <div className="card sticky-submit">
          {submitError && <p className="alert bad">{submitError}</p>}
          {uploadedCount === 0 && (
            <p className="muted small">Загрузите хотя бы один документ</p>
          )}
          <button
            className="primary"
            onClick={() => void submit()}
            disabled={submitting || uploadedCount === 0}
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

function NumberCard({
  session,
  publicNo,
  submitted,
}: {
  session: Session;
  publicNo: string;
  submitted: boolean;
}) {
  const [copied, setCopied] = useState("");
  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      window.prompt("Скопируйте вручную:", text);
    }
    setCopied(what);
    window.setTimeout(() => setCopied(""), 2000);
  };
  return (
    <div className={`card number ${submitted ? "confirm" : ""}`}>
      {submitted ? (
        <h2>
          <Icon name="ok" /> Комплект отправлен
        </h2>
      ) : (
        <h2>Загрузите документы</h2>
      )}
      {publicNo && (
        <div className="no-row">
          <span className="muted">Номер заявки</span>
          <b className="no" data-testid="public-no">
            {publicNo}
          </b>
          <button
            className="icon-btn"
            onClick={() => void copy(publicNo, "no")}
            aria-label="Скопировать номер"
          >
            <Icon name="copy" />
            {copied === "no" ? "Скопировано" : "Копировать"}
          </button>
        </div>
      )}
      <p className="muted small">
        {submitted
          ? "Документы проверяются автоматически, статусы ниже обновляются сами. Ссылку на заявку мы отправили на email."
          : "Сфотографируйте каждый документ или выберите файл. По каждому файлу сразу покажем, принят ли он."}
      </p>
      <button
        className="link left"
        onClick={() => void copy(linkFor(session), "link")}
      >
        {copied === "link"
          ? "Ссылка скопирована"
          : "Скопировать ссылку на заявку — по ней можно дозагрузить документы"}
      </button>
    </div>
  );
}

function Thumb({ src }: { src?: string }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken)
    return (
      <span className="thumb pdf">
        <Icon name="file" />
      </span>
    );
  return (
    <img className="thumb" src={src} alt="" onError={() => setBroken(true)} />
  );
}

function DocumentCard({
  doc,
  req,
  uploads,
  onFiles,
  isSubmitted,
}: {
  doc: AppDocument;
  req?: Requirement;
  uploads: Upload[];
  onFiles: (files: FileList | File[]) => void;
  isSubmitted: boolean;
}) {
  const camera = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const v = docView(doc);
  const hasFiles = doc.files.length > 0;
  const mine = new Set(uploads.map((u) => u.result?.file_id).filter(Boolean));
  const serverOnly = doc.files.filter((f) => !mine.has(f.file_id));
  const serverById = Object.fromEntries(doc.files.map((f) => [f.file_id, f]));
  const optional = req ? !req.required : doc.required === false;

  return (
    <section
      className={`card doc tone-${v.tone} ${drag ? "drag" : ""}`}
      data-testid={`doc-${doc.doc_type}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
      }}
    >
      <div className="doc-head">
        <h3>
          {doc.title}
          {optional && <span className="tag">необязательно</span>}
        </h3>
        <div className={`status ${v.tone}`} data-testid="doc-status">
          <Icon name={v.tone === "muted" ? "dot" : v.tone} />
          <div>
            <b>{v.title}</b>
            {v.detail && <span>{v.detail}</span>}
          </div>
        </div>
        {v.stage && (
          <div className="stagebar" aria-label={`Этап ${v.stage} из 4`}>
            {[1, 2, 3, 4].map((i) => (
              <span key={i} className={i <= v.stage! ? "on" : ""} />
            ))}
          </div>
        )}
      </div>

      {req?.hint && (
        <p className="hint">
          <Icon name="camera" size={16} /> {req.hint}
        </p>
      )}
      {doc.message && <p className={`msg ${v.tone}`}>{doc.message}</p>}

      {serverOnly.length > 0 && (
        <ul className="files" aria-label="Загруженные файлы">
          {serverOnly.map((f) => (
            <li key={f.file_id}>
              <Icon name="file" size={16} />
              <span className="fname">{f.name}</span>
              <span className={`fstat ${f.status}`}>
                {FILE_STATUS_RU[f.status] ?? f.status}
              </span>
              {f.error_message && <div className="ferr">{f.error_message}</div>}
            </li>
          ))}
        </ul>
      )}

      {uploads.length > 0 && (
        <ul className="uploads" aria-label="Результаты загрузки">
          {uploads.map((u) => (
            <li
              key={u.id}
              className={u.result ? (u.result.accepted ? "ok" : "bad") : "info"}
            >
              <Thumb src={u.thumb} />
              <div className="uinfo">
                <span className="fname">{u.name}</span>
                {!u.result && (
                  <div className="bar small">
                    <span
                      style={{ width: `${Math.round(u.progress * 100)}%` }}
                    />
                  </div>
                )}
                {u.result && (
                  <span className="ures" data-testid="upload-result">
                    {u.result.accepted ? (
                      <>
                        <Icon name="ok" size={14} /> Принят
                        {u.result.file_id && serverById[u.result.file_id] && (
                          <span
                            className={`fstat ${serverById[u.result.file_id].status}`}
                          >
                            {FILE_STATUS_RU[
                              serverById[u.result.file_id].status
                            ] ?? serverById[u.result.file_id].status}
                          </span>
                        )}
                      </>
                    ) : (
                      <>
                        <Icon name="bad" size={14} /> Отклонён:{" "}
                        {u.result.error?.message}
                      </>
                    )}
                  </span>
                )}
                {u.result?.file_id &&
                  serverById[u.result.file_id]?.error_message && (
                    <div className="ferr">
                      {serverById[u.result.file_id].error_message}
                    </div>
                  )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <input
        ref={camera}
        id={`cam-${doc.doc_type}`}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onFiles(Array.from(e.target.files));
          e.target.value = "";
        }}
      />
      <input
        ref={picker}
        id={`pick-${doc.doc_type}`}
        data-testid={`input-${doc.doc_type}`}
        type="file"
        multiple
        accept="image/*,application/pdf"
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onFiles(Array.from(e.target.files));
          e.target.value = "";
        }}
      />
      <div className="actions">
        <button className="secondary" onClick={() => camera.current?.click()}>
          <Icon name="camera" /> Сфотографировать
        </button>
        <button className="secondary" onClick={() => picker.current?.click()}>
          <Icon name="file" />{" "}
          {hasFiles || isSubmitted ? "Дозагрузить" : "Выбрать файл"}
        </button>
      </div>
      <p className="muted small drop-hint">
        {req ? `До ${req.max_files} файлов · ` : ""}JPG, PNG, HEIC или PDF до{" "}
        {MAX_MB} МБ · на компьютере можно перетащить файл сюда
      </p>
    </section>
  );
}
