import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { ProvidersResponse } from "../api/types";
import {
  choiceStatus,
  filterProviders,
  formatPrice,
  modelName,
  providerName,
  type ModelChoice,
} from "../lib/models";
import { Button } from "./ui/Button";
import { inputClass } from "./ui/Field";
import { RefreshIcon, SearchIcon } from "./ui/icons";

interface Props {
  providers: ProvidersResponse | null;
  loading: boolean;
  value: ModelChoice | null;
  onChange(choice: ModelChoice): void;
  onRefresh(): void;
  invalid?: boolean;
  describedBy?: string;
}

export function ModelPicker({
  providers,
  loading,
  value,
  onChange,
  onRefresh,
  invalid,
  describedBy,
}: Props) {
  const [query, setQuery] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const baseId = useId();

  const groups = useMemo(
    () => filterProviders(providers?.providers ?? [], query),
    [providers, query],
  );
  const status = choiceStatus(providers, value);

  const options = () =>
    Array.from(
      listRef.current?.querySelectorAll<HTMLElement>('[role="option"]:not([aria-disabled="true"])') ??
        [],
    );

  function onSearchKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      options()[0]?.focus();
    }
  }
  function onListKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const opts = options();
    const i = opts.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") opts[Math.min(i + 1, opts.length - 1)]?.focus();
    else if (i <= 0) searchRef.current?.focus();
    else opts[i - 1]?.focus();
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input
            ref={searchRef}
            type="search"
            aria-label="Search providers and models"
            placeholder="Search providers and models"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onSearchKey}
            aria-invalid={invalid || undefined}
            aria-describedby={describedBy}
            className={`${inputClass} pl-9`}
          />
        </div>
        <Button onClick={onRefresh} disabled={loading} aria-label="Refresh providers">
          <RefreshIcon className={loading ? "animate-spin" : ""} />
          <span className="hidden sm:inline">{loading ? "Refreshing" : "Refresh"}</span>
        </Button>
      </div>

      <div
        ref={listRef}
        role="listbox"
        aria-label="Models"
        onKeyDown={onListKey}
        className="scroll-thin max-h-56 overflow-y-auto rounded-lg border border-line-strong bg-surface"
      >
        {!providers && loading ? (
          <p className="p-3 text-sm text-muted">Loading models…</p>
        ) : !providers ? (
          <p className="p-3 text-sm text-muted">Could not load the model list. Try Refresh.</p>
        ) : groups.length === 0 ? (
          <p className="p-3 text-sm text-muted">No models match “{query.trim()}”.</p>
        ) : (
          groups.map((p) => {
            const headingId = `${baseId}-${p.id}`;
            return (
              <div key={p.id} role="group" aria-labelledby={headingId}>
                <div className="sticky top-0 flex items-center justify-between gap-2 border-b border-line bg-sunken px-3 py-1.5 text-xs">
                  <span id={headingId} className="font-semibold">
                    {p.name}
                  </span>
                  {p.connected ? (
                    <span className="rounded-full bg-ok-soft px-2 py-0.5 font-medium text-ok">
                      Connected
                    </span>
                  ) : (
                    <span className="rounded-full bg-warn-soft px-2 py-0.5 font-medium text-warn">
                      Not connected
                    </span>
                  )}
                </div>
                {!p.connected ? (
                  <p className="px-3 py-1.5 text-xs text-muted">
                    Connect {p.name} in OpenCode, then press Refresh.
                  </p>
                ) : null}
                {p.models.map((m) => {
                  const selected = value?.providerId === p.id && value.modelId === m.id;
                  const price = formatPrice(m);
                  return (
                    <button
                      key={m.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      aria-disabled={!p.connected || undefined}
                      onClick={() => p.connected && onChange({ providerId: p.id, modelId: m.id })}
                      className={`flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left text-sm ${
                        p.connected
                          ? selected
                            ? "bg-accent-soft"
                            : "hover:bg-sunken"
                          : "cursor-not-allowed text-muted"
                      }`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{m.name}</span>
                        <span className="block truncate text-xs text-muted">{m.id}</span>
                      </span>
                      <span className="shrink-0 text-right text-xs text-muted">
                        {price ?? (p.connected ? "Price not listed" : "")}
                      </span>
                    </button>
                  );
                })}
              </div>
            );
          })
        )}
      </div>

      <p className="text-sm" aria-live="polite">
        {value ? (
          <>
            <span className="text-muted">Selected: </span>
            <span className="font-medium">{providerName(providers, value.providerId)}</span>
            <span className="text-muted"> / </span>
            <span className="font-medium">
              {modelName(providers, value.providerId, value.modelId)}
            </span>
          </>
        ) : (
          <span className="text-muted">No model selected.</span>
        )}
      </p>
      {status === "disconnected" ? (
        <p className="text-xs font-medium text-warn">
          This provider is not connected. Connect it in OpenCode and press Refresh, or choose
          another model.
        </p>
      ) : status === "missing" ? (
        <p className="text-xs font-medium text-warn">
          This model is not in the current catalog. Choose another model.
        </p>
      ) : null}
    </div>
  );
}
