// Клиент публичного API. Все проверки делает сервер; здесь только запросы и разбор ошибок.
// Токен заявки передаётся в заголовке X-Application-Token и хранится в localStorage и в hash ссылки.

export type Country = { code: string; name_ru: string; group: string };
export type Requirement = {
  doc_type: string;
  title: string;
  hint: string;
  max_files: number;
  required: boolean;
};
export type Session = {
  application_id: string;
  token: string;
  public_no: string;
};
export type FileResult = {
  name: string;
  accepted: boolean;
  file_id?: string;
  error?: { code: string; message: string };
};
export type AppFile = {
  file_id: string;
  name: string;
  status: string;
  error_message?: string | null;
};
export type AppDocument = {
  doc_type: string;
  title: string;
  status: string;
  stage?: string | null;
  message?: string | null;
  files: AppFile[];
};
export type AppState = {
  public_no: string;
  status: string;
  documents: AppDocument[];
  missing: string[];
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const GENERIC = "Сервис временно недоступен. Попробуйте ещё раз через минуту.";

async function request<T>(
  url: string,
  init: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", "fetch");
  if (token) headers.set("X-Application-Token", token);
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(
      0,
      "NETWORK",
      "Нет связи с сервером. Проверьте интернет и попробуйте снова.",
    );
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string } } | null)
      ?.error;
    throw new ApiError(
      res.status,
      err?.code ?? "HTTP_" + res.status,
      err?.message ?? GENERIC,
    );
  }
  return body as T;
}

export const api = {
  countries: () => request<Country[]>("/api/public/countries"),
  requirements: (country: string) =>
    request<{ documents: Requirement[] }>(
      `/api/public/requirements?country=${encodeURIComponent(country)}`,
    ),
  createApplication: (data: {
    country_code: string;
    full_name: string;
    email: string;
    consent_accepted: boolean;
  }) =>
    request<Session>("/api/public/applications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  // Каждый файл — отдельный запрос: так соискатель видит результат по каждому файлу сразу.
  uploadFile: async (
    s: Session,
    docType: string,
    file: File,
  ): Promise<FileResult> => {
    const form = new FormData();
    form.append("doc_type", docType);
    form.append("files", file, file.name);
    try {
      const r = await request<{ results: FileResult[] }>(
        `/api/public/applications/${s.application_id}/files`,
        { method: "POST", body: form },
        s.token,
      );
      return (
        r.results[0] ?? {
          name: file.name,
          accepted: false,
          error: { code: "EMPTY", message: GENERIC },
        }
      );
    } catch (e) {
      const err = e as ApiError;
      return {
        name: file.name,
        accepted: false,
        error: { code: err.code, message: err.message },
      };
    }
  },
  submit: (s: Session) =>
    request<{ public_no: string; missing: string[] }>(
      `/api/public/applications/${s.application_id}/submit`,
      { method: "POST" },
      s.token,
    ),
  state: (s: Session) =>
    request<AppState>(
      `/api/public/applications/${s.application_id}`,
      {},
      s.token,
    ),
};

const KEY = "hr_application";

export function saveSession(s: Session): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* приватный режим браузера — ссылка в hash всё равно работает */
  }
  window.location.hash = `#/a/${s.application_id}/${s.token}`;
}

export function loadSession(): Session | null {
  const m = window.location.hash.match(/^#\/a\/([^/]+)\/([^/]+)$/);
  let stored: Session | null = null;
  try {
    stored = JSON.parse(localStorage.getItem(KEY) ?? "null") as Session | null;
  } catch {
    stored = null;
  }
  if (m) {
    const [, id, token] = m;
    return {
      application_id: id,
      token,
      public_no: stored?.application_id === id ? stored.public_no : "",
    };
  }
  return stored;
}

export function clearSession(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  history.replaceState(null, "", window.location.pathname);
}
