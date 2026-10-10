// Общие элементы интерфейса: всплывающие уведомления (тосты), скелетоны загрузки,
// пустые состояния и бейджи статуса. Без UI-библиотек — только React и CSS.
import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { statusRu, statusTone } from "./api";

type Toast = { id: number; tone: "ok" | "bad" | "info"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(
  () => {},
);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, tone, text }]);
    setTimeout(
      () => setItems((xs) => xs.filter((t) => t.id !== id)),
      tone === "bad" ? 7000 : 3500,
    );
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

export function Skeleton({
  lines = 4,
  height = 14,
}: {
  lines?: number;
  height?: number;
}) {
  return (
    <div className="skeleton-block" aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{ height, width: `${70 + ((i * 13) % 30)}%` }}
        />
      ))}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge tone-${statusTone(status)}`}>
      {statusRu(status)}
    </span>
  );
}

export function ErrorBox({
  text,
  onRetry,
}: {
  text: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-box">
      <span>{text}</span>
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}
