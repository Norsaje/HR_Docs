// Тонкий клиент API оператора. Сессия — HttpOnly-cookie BFF (браузер не видит токенов).
// Для POST/PATCH обязателен X-Requested-With: без него сервер отвечает 403 (защита от CSRF).
// Ошибка сервера — всегда {error:{code, message}}; message уже по-русски.

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
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
    const err = (data as { error?: { code?: string; message?: string } } | null)
      ?.error;
    throw new ApiError(
      res.status,
      err?.code ?? "HTTP_" + res.status,
      err?.message ?? `Ошибка сервера (${res.status}). Повторите позже.`,
    );
  }
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  patch: <T>(url: string, body: unknown) => request<T>("PATCH", url, body),
};

// ---- Типы ответов (по контракту бэкенда) ----

export interface Me {
  name: string;
  roles: string[];
  offices?: string[];
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
}

export interface Issue {
  severity: "error" | "warning" | "info";
  message: string;
}

export interface Field {
  id: string;
  key: string;
  label: string;
  value: string | null;
  confidence: "green" | "yellow" | "red" | "gray";
  source: "model" | "operator";
  edited_by?: string | null;
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
    job_id: string;
    file_id: string;
    worker: string;
    stage: string;
    attempts: number;
    started_at: string;
  }[];
  failed: {
    job_id: string;
    file_id: string;
    attempts: number;
    error: string;
    at: string;
  }[];
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
};

export function statusRu(s: string): string {
  return STATUS_RU[s] ?? s;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}
