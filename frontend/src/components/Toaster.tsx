import { useToasts } from "../state/toast";
import { AlertIcon, CheckIcon, CloseIcon } from "./ui/icons";

export function Toaster() {
  const { toasts, dismiss } = useToasts();
  return (
    <div
      aria-label="Notifications"
      className="pointer-events-none fixed inset-x-3 bottom-3 z-[60] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-96"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className={`animate-pop pointer-events-auto flex w-full items-start gap-2.5 rounded-xl border px-3.5 py-3 text-sm shadow-lg ${
            t.kind === "error"
              ? "border-danger/40 bg-danger-soft text-danger"
              : "border-line bg-surface text-fg"
          }`}
        >
          {t.kind === "error" ? (
            <AlertIcon className="mt-0.5 shrink-0" />
          ) : (
            <CheckIcon className="mt-0.5 shrink-0 text-ok" />
          )}
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{t.title}</p>
            {t.message ? <p className="mt-0.5 break-words">{t.message}</p> : null}
          </div>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss notification"
            className="-mr-1 rounded-md p-1 opacity-70 hover:opacity-100"
          >
            <CloseIcon width={16} height={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
