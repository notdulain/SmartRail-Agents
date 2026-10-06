import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { describeError } from "../lib/errors";

export interface Toast {
  id: number;
  kind: "info" | "error";
  title: string;
  message?: string;
}

interface ToastApi {
  toasts: Toast[];
  info(title: string, message?: string): void;
  error(err: unknown): void;
  dismiss(id: number): void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (toast: Omit<Toast, "id">, ttl: number) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { ...toast, id }]);
      setTimeout(() => dismiss(id), ttl);
    },
    [dismiss],
  );

  const value = useMemo<ToastApi>(
    () => ({
      toasts,
      dismiss,
      info: (title, message) => push({ kind: "info", title, message }, 5000),
      error: (err) => {
        const d = describeError(err);
        push({ kind: "error", title: d.title, message: d.message }, 12000);
      },
    }),
    [toasts, dismiss, push],
  );
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

export function useToasts(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("ToastProvider missing");
  return ctx;
}
