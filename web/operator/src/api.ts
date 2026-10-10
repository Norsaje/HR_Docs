// Тонкий клиент API оператора. Сессия — HttpOnly-cookie BFF (браузер не видит токенов).
// Для POST/PATCH обязателен X-Requested-With: без него сервер отвечает 403 (защита от CSRF).
// Ошибка сервера — всегда {error:{code, message, support_id}}; message уже по-русски.

export class ApiError extends Error {
  status: number;
  code: string;
  supportId?: string;
  constructor(
    status: number,
    code: string,
    message: string,
    supportId?: string,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.supportId = supportId;
  }
  /** Текст для человека: сообщение и код обращения в поддержку, если он есть. */
  get human(): string {
    return this.supportId
      ? `${this.message} Код обращения: ${this.supportId}`
      : this.message;
  }
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.human;
  return "Что-то пошло не так. Повторите действие.";
}

function goLogin(): void {
  const back = window.location.pathname + window.location.hash;
  window.location.href =
    "/api/auth/login?return_to=" + encodeURIComponent(back);
}

async function request<T>(
  method: string,
  url: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-Requested-With"] = "fetch";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      0,
      "NETWORK",
      "Нет связи с сервером. Проверьте подключение и повторите.",
    );
  }
  if (res.status === 401) {
    goLogin();
    throw new ApiError(401, "UNAUTHORIZED", "Требуется вход");
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const err = (
      data as {
        error?: { code?: string; message?: string; support_id?: string };
      } | null
    )?.error;
    const fallback =
      res.status === 404
        ? "Функция пока недоступна на сервере или объект не найден."
        : `Ошибка сервера (${res.status}). Повторите позже.`;
    throw new ApiError(
      res.status,
      err?.code ?? "HTTP_" + res.status,
      err?.message ?? fallback,
      err?.support_id,
    );
  }
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  patch: <T>(url: string, body: unknown) => request<T>("PATCH", url, body),
};

// ---- Типы ответов (по контракту бэкенда; новые поля — необязательные) ----

export interface Me {
  name: string;
  roles: string[];
  offices?: string[];
}

export const canEdit = (me: Me) =>
  me.roles.some((r) => r === "doc-editor" || r === "supervisor");
export const isSupervisor = (me: Me) => me.roles.includes("supervisor");

export interface DocChip {
  doc_type: string;
  title: string;
  status: string;
  errors?: number;
}

export interface AppListItem {
  id: string;
  public_no: string;
  full_name: string;
  email_masked: string;
  country_code: string;
  status: string;
  created_at: string;
  documents_total: number;
  documents_ready: number;
  has_errors: boolean;
  errors_count?: number;
  documents?: DocChip[];
}

export interface Issue {
  severity: "error" | "warning" | "info";
  message: string;
}

export type Confidence = "green" | "yellow" | "red" | "grey" | "gray";

export interface Field {
  id: string;
  key: string;
  label: string;
  value: string | null;
  raw?: string | null;
  status?: string;
  confidence: Confidence;
  signals?: string[];
  signals_ru?: string[];
  candidates?: {
    ocr?: string | null;
    mrz?: string | null;
    hints?: string[];
  } | null;
  hint?: string | null;
  source: "model" | "operator";
  edited_by?: string | null;
  edited_at?: string | null;
  bbox?: number[] | null;
  file_id?: string | null;
}

export interface DocFile {
  file_id: string;
  status?: string;
  error_message?: string | null;
}

export interface Doc {
  id: string;
  doc_type: string;
  title: string;
  status: string;
  stage?: string | null;
  message?: string | null;
  expires_on?: string | null;
  verified_by?: string | null;
  verified_at?: string | null;
  issues: Issue[];
  files: DocFile[];
  fields: Field[];
}

export interface Card {
  application: {
    id: string;
    public_no: string;
    full_name: string;
    email_masked: string;
    country_code: string;
    status: string;
    created_at: string;
    missing?: string[];
    allowed_doc_types?: { doc_type: string; title: string }[];
  };
  documents: Doc[];
  issues?: Issue[];
  crosscheck?: Issue[];
}

export interface HistoryItem {
  at: string;
  actor: string;
  action: string;
  message: string;
}

export interface QueueStats {
  waiting: number;
  processing: number;
  done_last_hour: number;
  failed_last_hour: number;
  avg_duration_s: number | null;
  p95_duration_s?: number | null;
  oldest_waiting_s?: number | null;
  in_progress: {
    job_id: string | number;
    file_id: string;
    worker: string;
    stage: string;
    attempts: number;
    started_at: string;
  }[];
  failed: {
    job_id: string | number;
    file_id: string;
    attempts: number;
    error: string;
    at: string;
  }[];
  retrying?: {
    job_id: string | number;
    attempts: number;
    error: string;
    run_after: string;
  }[];
}

export interface ModelStatus {
  llm_ok: boolean;
  ocr_ok?: boolean | null;
  paused?: boolean;
  detail_ru?: string | null;
}

export interface ExpiringItem {
  application_id: string;
  public_no: string;
  full_name: string;
  doc_type: string;
  title: string;
  expires_on: string;
  days_left: number;
}

export interface StaffUser {
  username: string;
  name?: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  roles?: string[];
  office?: string | null;
  enabled?: boolean;
}

// ---- Подписи для людей ----

export const STATUS_RU: Record<string, string> = {
  draft: "черновик",
  submitted: "отправлена",
  processing: "разбирается",
  ready: "готово",
  needs_review: "есть замечания",
  unreadable: "не прочитался",
  failed: "ошибка разбора",
  uploaded: "загружен",
  queued: "в очереди",
  missing: "не загружен",
  done: "разобран",
  verified: "проверено",
};

export function statusRu(s: string): string {
  return STATUS_RU[s] ?? s;
}

/** Тон статуса для бейджа: ok / warn / bad / info. */
export function statusTone(s: string): "ok" | "warn" | "bad" | "info" {
  if (s === "ready" || s === "done" || s === "verified") return "ok";
  if (s === "needs_review") return "warn";
  if (s === "failed" || s === "unreadable" || s === "missing") return "bad";
  return "info";
}

export const STAGE_RU: Record<string, string> = {
  queued: "в очереди",
  preprocess: "подготовка",
  extract: "чтение моделью",
  ocr: "OCR",
  verify: "проверки",
  done: "готово",
};

export const CONF_RU: Record<string, string> = {
  red: "нужно проверить",
  yellow: "стоит взглянуть",
  green: "подтверждено",
  grey: "нет на документе",
  gray: "нет на документе",
};

const SIGNAL_RU: Record<string, string> = {
  checksum_ok: "контрольная сумма сошлась",
  checksum_fail: "контрольная сумма не сошлась",
  mrz_match: "совпало с машиночитаемой зоной",
  mrz_mismatch: "расходится с машиночитаемой зоной",
  ocr_agree: "OCR прочитал то же значение",
  ocr_disagree: "OCR прочитал иначе",
  ocr_unavailable: "OCR недоступен — подтвердить нечем",
  text_layer_match: "совпало с текстовым слоем PDF",
  single_source: "прочитал один источник — подтвердить нечем",
  rule_fail: "нарушено правило проверки",
  unreadable: "модель не смогла прочитать",
  absent: "поля нет на документе",
  handwritten: "вписано от руки или в штампе",
  operator_edit: "исправлено оператором",
  region_blurry: "область поля размыта",
};

export function signalRu(code: string): string {
  return SIGNAL_RU[code] ?? code;
}

export const CONF_RANK: Record<string, number> = {
  red: 0,
  yellow: 1,
  green: 2,
  grey: 3,
  gray: 3,
};

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("ru-RU");
}
