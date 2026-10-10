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
  required?: boolean;
  status: string;
  status_ru?: string;
  stage?: string | null;
  stage_ru?: string | null;
  message?: string | null;
  files: AppFile[];
};
export type AppState = {
  public_no: string;
  status: string;
  documents: AppDocument[];
  missing: string[];
  email_confirmed?: boolean;
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
const NETWORK = "Нет связи с сервером. Проверьте интернет и попробуйте снова.";

function parseError(status: number, text: string): ApiError {
  let err: { code?: string; message?: string } | undefined;
  try {
    err = (JSON.parse(text) as { error?: { code?: string; message?: string } })
      .error;
  } catch {
    err = undefined;
  }
  return new ApiError(
    status,
    err?.code ?? "HTTP_" + status,
    err?.message ?? GENERIC,
  );
}

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
    throw new ApiError(0, "NETWORK", NETWORK);
  }
  const text = await res.text();
  if (!res.ok) throw parseError(res.status, text);
  return (text ? JSON.parse(text) : null) as T;
}

// Загрузка одного файла через XMLHttpRequest: у fetch нет события прогресса отправки.
function uploadWithProgress(
  url: string,
  form: FormData,
  token: string,
  onProgress: (fraction: number) => void,
): Promise<{ results: FileResult[] }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Requested-With", "fetch");
    xhr.setRequestHeader("X-Application-Token", token);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as { results: FileResult[] });
        } catch {
          reject(new ApiError(xhr.status, "BAD_JSON", GENERIC));
        }
      } else reject(parseError(xhr.status, xhr.responseText));
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK", NETWORK));
    xhr.send(form);
  });
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
    onProgress: (fraction: number) => void,
  ): Promise<FileResult> => {
    const form = new FormData();
    form.append("doc_type", docType);
    form.append("files", file, file.name);
    try {
      const r = await uploadWithProgress(
        `/api/public/applications/${s.application_id}/files`,
        form,
        s.token,
        onProgress,
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
  confirmEmail: (s: Session, code: string) =>
    request<{ email_confirmed: boolean }>(
      `/api/public/applications/${s.application_id}/confirm-email`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      },
      s.token,
    ),
  resendCode: (s: Session) =>
    request<{ sent: boolean }>(
      `/api/public/applications/${s.application_id}/resend-code`,
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
    const s = {
      application_id: id,
      token,
      public_no: stored?.application_id === id ? stored.public_no : "",
    };
    try {
      localStorage.setItem(KEY, JSON.stringify(s));
    } catch {
      /* ignore */
    }
    return s;
  }
  // После перезагрузки без hash возвращаем ссылку в адресную строку — её можно скопировать.
  if (stored)
    window.location.hash = `#/a/${stored.application_id}/${stored.token}`;
  return stored;
}

export function linkFor(s: Session): string {
  return `${window.location.origin}/#/a/${s.application_id}/${s.token}`;
}

export function clearSession(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  history.replaceState(null, "", window.location.pathname);
}
