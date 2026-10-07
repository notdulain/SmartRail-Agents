import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, ApiError } from "../api/client";
import type { DirectoryListing } from "../api/types";
import { breadcrumbs, rootOf } from "../lib/paths";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { inputClass } from "./ui/Field";
import { ArrowUpIcon, FolderIcon, HomeIcon, SpinnerIcon } from "./ui/icons";

interface Props {
  open: boolean;
  /** Folder to start in (the current working directory); the user's home when empty. */
  initialPath?: string | null;
  onSelect(path: string): void;
  onClose(): void;
}

/**
 * Browses the backend machine's folders (a browser cannot hand us absolute paths) and
 * returns the chosen one.
 */
export function FolderPickerDialog({ open, initialPath, onSelect, onClose }: Props) {
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState(initialPath ?? "");
  const [active, setActive] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const pathRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const navigate = useCallback(async (path?: string, fallbackToHome = false) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    try {
      const next = await api.listDirectories(path || undefined, ac.signal);
      if (ac.signal.aborted) return;
      setListing(next);
      setTyped(next.path);
      setActive(0);
      setError(null);
    } catch (err) {
      if (ac.signal.aborted) return;
      const message =
        err instanceof ApiError ? err.message : "Could not read that folder. Try another one.";
      setError(path ? `Cannot open ${path}: ${message}` : message);
      if (fallbackToHome && path) {
        // The saved folder may have been moved or deleted: start from home instead, keeping
        // the explanation visible.
        try {
          const home = await api.listDirectories(undefined, ac.signal);
          if (ac.signal.aborted) return;
          setListing(home);
          setTyped(home.path);
          setActive(0);
        } catch {
          // keep the first error
        }
      }
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setListing(null);
    setError(null);
    setTyped(initialPath ?? "");
    void navigate(initialPath ?? undefined, true);
    return () => abortRef.current?.abort();
  }, [open, initialPath, navigate]);

  function go(e: FormEvent) {
    // This form is portalled out of the agent form, but React events still bubble to it.
    e.preventDefault();
    e.stopPropagation();
    const path = typed.trim();
    if (path) void navigate(path);
  }

  function onListKey(e: KeyboardEvent<HTMLUListElement>) {
    const entries = listing?.entries ?? [];
    if (entries.length === 0) return;
    let next = active;
    if (e.key === "ArrowDown") next = Math.min(active + 1, entries.length - 1);
    else if (e.key === "ArrowUp") next = Math.max(active - 1, 0);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = entries.length - 1;
    else if (e.key === "Backspace" && listing?.parent) {
      e.preventDefault();
      void navigate(listing.parent);
      return;
    } else return;
    e.preventDefault();
    setActive(next);
    listRef.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  }

  const crumbs = listing ? breadcrumbs(listing.path, listing.roots) : [];
  const currentRoot = listing ? rootOf(listing.path, listing.roots) : null;
  const atHome = !!listing && listing.path === listing.home;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Choose a working directory"
      description="The agent's file tools can only reach files inside this folder."
      size="max-w-xl"
      initialFocus={pathRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!listing || loading}
            onClick={() => listing && onSelect(listing.path)}
          >
            Select this folder
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <form onSubmit={go} className="flex gap-2" role="search" aria-label="Go to a folder">
          <label htmlFor="folder-path" className="sr-only">
            Folder path
          </label>
          <input
            id="folder-path"
            ref={pathRef}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="Type or paste a folder path"
            autoComplete="off"
            spellCheck={false}
            className={`${inputClass} font-mono text-[0.8rem]`}
          />
          <Button type="submit" disabled={!typed.trim() || loading}>
            Go
          </Button>
        </form>

        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => listing?.parent && void navigate(listing.parent)}
            disabled={!listing?.parent || loading}
            aria-label="Up to parent folder"
            title="Up to parent folder"
          >
            <ArrowUpIcon width={16} height={16} />
            Up
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => listing && void navigate(listing.home)}
            disabled={!listing || atHome || loading}
            title={listing ? `Home (${listing.home})` : "Home"}
          >
            <HomeIcon width={16} height={16} />
            Home
          </Button>
          {listing && listing.roots.length > 1 ? (
            <>
              <label htmlFor="folder-root" className="ml-auto text-xs text-muted">
                Drive
              </label>
              <select
                id="folder-root"
                value={currentRoot ?? ""}
                onChange={(e) => e.target.value && void navigate(e.target.value)}
                disabled={loading}
                className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-sm"
              >
                {currentRoot ? null : <option value="">—</option>}
                {listing.roots.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </>
          ) : null}
        </div>

        {crumbs.length > 0 ? (
          <nav aria-label="Current folder" className="min-w-0">
            <ol className="flex flex-wrap items-center gap-0.5 text-sm">
              {crumbs.map((c, i) => {
                const last = i === crumbs.length - 1;
                return (
                  <li key={c.path} className="flex min-w-0 items-center gap-0.5">
                    {i > 0 ? (
                      <span aria-hidden="true" className="text-muted">
                        /
                      </span>
                    ) : null}
                    {last ? (
                      <span aria-current="location" className="truncate px-1 font-semibold">
                        {c.name}
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void navigate(c.path)}
                        disabled={loading}
                        className="truncate rounded px-1 text-accent hover:bg-sunken hover:underline"
                      >
                        {c.name}
                      </button>
                    )}
                  </li>
                );
              })}
            </ol>
          </nav>
        ) : null}

        {error ? (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        ) : null}

        <div className="relative h-72 overflow-hidden rounded-lg border border-line">
          {loading ? (
            <div
              role="status"
              className="absolute inset-0 z-10 flex items-center justify-center gap-2 bg-surface/70 text-sm text-muted"
            >
              <SpinnerIcon width={16} height={16} />
              Loading folders…
            </div>
          ) : null}
          {listing && listing.entries.length === 0 ? (
            <p className="p-4 text-sm text-muted">No sub-folders here.</p>
          ) : listing ? (
            <ul
              ref={listRef}
              aria-label="Sub-folders"
              onKeyDown={onListKey}
              className="scroll-thin h-full overflow-y-auto py-1"
            >
              {listing.entries.map((entry, i) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    tabIndex={i === active ? 0 : -1}
                    onFocus={() => setActive(i)}
                    onClick={() => void navigate(entry.path)}
                    title={entry.path}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-sunken focus-visible:bg-sunken focus-visible:outline-offset-[-2px]"
                  >
                    <FolderIcon width={16} height={16} className="shrink-0 text-muted" />
                    <span className="truncate">{entry.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <p className="text-xs text-muted">
          Open a folder to look inside, then choose <strong>Select this folder</strong>. Arrow keys
          move through the list; Backspace goes up.
        </p>
      </div>
    </Dialog>
  );
}
