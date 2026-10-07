import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { api } from "../api/client";
import { MAX_ATTACHMENTS, type FileEntry, type FileRef } from "../api/types";
import { AttachmentChip } from "./AttachmentChip";
import { Button } from "./ui/Button";
import { FileIcon, FolderIcon, SendIcon, SpinnerIcon, StopIcon } from "./ui/icons";

export const MESSAGE_MAX = 20000;
/** Wait this long after the last keystroke before searching files. */
export const MENTION_DEBOUNCE_MS = 150;

/** A participant whose working directory can be searched with `@`. */
export interface MentionSource {
  agentId: string;
  agentName: string;
}

interface Props {
  placeholder: string;
  /** A run is active or a message is being sent: the input is locked. */
  disabled: boolean;
  /** A run is active: show Stop instead of Send. */
  running: boolean;
  stopping: boolean;
  /** Participants with a working directory. Empty: `@` is plain text. */
  mentionSources?: MentionSource[];
  /** Label files with their agent's name (group chats). Defaults to "more than one source". */
  labelSources?: boolean;
  onSend(text: string, attachments: FileRef[]): Promise<boolean>;
  onStop(): void;
}

interface Mention {
  /** Index of the `@` in the text. */
  start: number;
  /** Text between the `@` and the caret. */
  query: string;
}

/** After picking a folder the mention stays open across the inserted path (which may hold spaces). */
interface Anchor {
  start: number;
  prefix: string;
}

interface ResultGroup {
  source: MentionSource;
  files: FileEntry[];
  truncated: boolean;
  error: boolean;
}

interface Option {
  id: string;
  source: MentionSource;
  entry: FileEntry;
}

/** Finds the `@token` that ends at the caret, if any. */
export function findMention(text: string, caret: number, anchor: Anchor | null): Mention | null {
  if (anchor && text[anchor.start] === "@" && caret > anchor.start) {
    const query = text.slice(anchor.start + 1, caret);
    if (query.startsWith(anchor.prefix) && !/\s/.test(query.slice(anchor.prefix.length))) {
      return { start: anchor.start, query };
    }
  }
  const m = /(^|\s)@([^\s@]*)$/.exec(text.slice(0, caret));
  if (!m) return null;
  return { start: caret - m[2].length - 1, query: m[2] };
}

export function Composer({
  placeholder,
  disabled,
  running,
  stopping,
  mentionSources = [],
  labelSources,
  onSend,
  onStop,
}: Props) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [attachments, setAttachments] = useState<FileRef[]>([]);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  /** `@` position the user closed with Escape; stays closed until a new mention starts. */
  const [dismissed, setDismissed] = useState<number | null>(null);
  const [results, setResults] = useState<{ query: string; groups: ResultGroup[] } | null>(null);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const wasDisabled = useRef(disabled);
  const pendingCaret = useRef<number | null>(null);
  const blurred = useRef(false);
  const listId = useId();

  const mention =
    mentionSources.length > 0 && !disabled ? findMention(text, caret, anchor) : null;
  const open = !!mention && mention.start !== dismissed;
  const grouped = labelSources ?? mentionSources.length > 1;
  const sourcesKey = mentionSources.map((s) => s.agentId).join(",");

  // Opening a conversation puts the cursor in the composer (not on touch screens, where it
  // would pop the keyboard up).
  useEffect(() => {
    if (window.matchMedia?.("(pointer: fine)").matches && !disabled) ref.current?.focus();
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    if (pendingCaret.current !== null) {
      el.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
  }, [text]);

  // Give focus back after a run finishes (the browser drops it when the field is disabled).
  useEffect(() => {
    if (wasDisabled.current && !disabled && document.activeElement === document.body) {
      ref.current?.focus();
    }
    wasDisabled.current = disabled;
  }, [disabled]);

  // Debounced search of every participant's folder for the text after `@`.
  const query = open ? mention.query : null;
  useEffect(() => {
    if (query === null) {
      setResults(null);
      setSearching(false);
      return;
    }
    const ac = new AbortController();
    setSearching(true);
    const timer = setTimeout(async () => {
      const limit = mentionSources.length > 1 ? 8 : 20;
      const groups = await Promise.all(
        mentionSources.map(async (source): Promise<ResultGroup> => {
          try {
            const res = await api.searchAgentFiles(source.agentId, query, limit, ac.signal);
            return { source, files: res.files, truncated: res.truncated, error: false };
          } catch {
            return { source, files: [], truncated: false, error: true };
          }
        }),
      );
      if (ac.signal.aborted) return;
      setResults({ query, groups });
      setActive(0);
      setSearching(false);
    }, MENTION_DEBOUNCE_MS);
    return () => {
      ac.abort();
      clearTimeout(timer);
    };
    // mentionSources is tracked through sourcesKey.
  }, [query, sourcesKey]);

  const options: Option[] = [];
  for (const g of results?.groups ?? []) {
    for (const entry of g.files) {
      options.push({ id: `${listId}-${options.length}`, source: g.source, entry });
    }
  }
  const activeIndex = Math.min(active, Math.max(options.length - 1, 0));

  const trimmed = text.trim();
  const tooLong = text.length > MESSAGE_MAX;
  const canSend = !disabled && trimmed.length > 0 && !tooLong;

  async function submit() {
    if (!canSend) return;
    const ok = await onSend(text, attachments);
    if (ok) {
      setText("");
      setCaret(0);
      setAttachments([]);
      setAnchor(null);
      setDismissed(null);
      setNotice(null);
    }
  }

  function replaceMention(m: Mention, insert: string) {
    const before = text.slice(0, m.start);
    let after = text.slice(m.start + 1 + m.query.length);
    if (!insert && /\s$/.test(before) && /^\s/.test(after)) after = after.slice(1);
    const next = before + insert + after;
    const pos = before.length + insert.length;
    pendingCaret.current = pos;
    setText(next);
    setCaret(pos);
  }

  function pick(option: Option) {
    if (!mention) return;
    const { entry, source } = option;
    if (entry.is_dir) {
      const prefix = entry.path.endsWith("/") ? entry.path : `${entry.path}/`;
      setAnchor({ start: mention.start, prefix });
      replaceMention(mention, `@${prefix}`);
      return;
    }
    setAnchor(null);
    replaceMention(mention, "");
    const exists = attachments.some((a) => a.agent_id === source.agentId && a.path === entry.path);
    if (exists) {
      setNotice(`${entry.path} is already attached.`);
    } else if (attachments.length >= MAX_ATTACHMENTS) {
      setNotice(`You can attach up to ${MAX_ATTACHMENTS} files to one message.`);
    } else {
      setAttachments([...attachments, { agent_id: source.agentId, path: entry.path }]);
      setNotice(null);
    }
    ref.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (open && !e.nativeEvent.isComposing) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (options.length === 0) return;
        const step = e.key === "ArrowDown" ? 1 : -1;
        setActive((activeIndex + step + options.length) % options.length);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        blurred.current = false;
        setDismissed(mention.start);
        setAnchor(null);
        return;
      }
      if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Tab" && !e.shiftKey)) {
        // Never send while choosing a file.
        if (options.length > 0 && results?.query === mention.query) {
          e.preventDefault();
          pick(options[activeIndex]);
        } else if (e.key === "Enter") {
          e.preventDefault();
        }
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  }

  function syncCaret() {
    const el = ref.current;
    if (el) setCaret(el.selectionStart ?? el.value.length);
  }

  const stale = searching || results?.query !== query;
  const nothingFound =
    !stale && options.length === 0 && results?.groups.every((g) => !g.error);
  const status = !open
    ? ""
    : stale
      ? "Searching files…"
      : options.length === 0
        ? "No matching files."
        : `${options.length} ${options.length === 1 ? "match" : "matches"}. Use the arrow keys and Enter to choose.`;

  return (
    <div className="border-t border-line bg-bg px-4 pb-4 pt-3 sm:px-6">
      <div className="relative mx-auto w-full max-w-3xl">
        {open ? (
          <div className="animate-pop absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-line bg-surface shadow-lg">
            <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs text-muted">
              {stale ? <SpinnerIcon width={14} height={14} /> : <FileIcon width={14} height={14} />}
              <span className="min-w-0 flex-1 truncate">
                {mention.query ? (
                  <>
                    Files matching <span className="font-mono text-fg">{mention.query}</span>
                  </>
                ) : (
                  "Attach a file. Type to search; recent files first."
                )}
              </span>
              <span className="hidden sm:inline">Esc to close</span>
            </div>
            <div
              id={listId}
              role="listbox"
              aria-label="Files to attach"
              className="scroll-thin max-h-64 overflow-y-auto py-1"
            >
              {(results?.groups ?? []).map((g) => {
                const groupOptions = options.filter((o) => o.source === g.source);
                if (!grouped && groupOptions.length === 0 && !g.error) return null;
                const body = (
                  <>
                    {groupOptions.map((o) => {
                      const i = options.indexOf(o);
                      const selected = i === activeIndex;
                      return (
                        <div
                          key={o.id}
                          id={o.id}
                          role="option"
                          aria-selected={selected}
                          onMouseDown={(e) => e.preventDefault()}
                          onMouseEnter={() => setActive(i)}
                          onClick={() => pick(o)}
                          className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm ${
                            selected ? "bg-accent-soft" : ""
                          }`}
                        >
                          {o.entry.is_dir ? (
                            <FolderIcon width={15} height={15} className="shrink-0 text-muted" />
                          ) : (
                            <FileIcon width={15} height={15} className="shrink-0 text-muted" />
                          )}
                          <span className="min-w-0 flex-1 truncate font-mono text-[0.8rem]">
                            {o.entry.path}
                            {o.entry.is_dir ? "/" : ""}
                          </span>
                          {o.entry.is_dir ? (
                            <span className="shrink-0 text-xs text-muted">folder</span>
                          ) : o.entry.size != null ? (
                            <span className="shrink-0 text-xs text-muted">
                              {formatSize(o.entry.size)}
                            </span>
                          ) : null}
                        </div>
                      );
                    })}
                    {g.error ? (
                      <p className="px-3 py-1 text-xs text-danger">
                        Could not search {grouped ? `${g.source.agentName}'s` : "the"} folder.
                      </p>
                    ) : grouped && groupOptions.length === 0 && !stale ? (
                      <p className="px-3 py-1 text-xs text-muted">No matches.</p>
                    ) : null}
                    {g.truncated && !stale ? (
                      <p className="px-3 py-1 text-xs text-muted">More files match; keep typing.</p>
                    ) : null}
                  </>
                );
                return grouped ? (
                  <div key={g.source.agentId} role="group" aria-label={g.source.agentName}>
                    <div
                      aria-hidden="true"
                      className="px-3 pb-0.5 pt-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted"
                    >
                      {g.source.agentName}
                    </div>
                    {body}
                  </div>
                ) : (
                  <div key={g.source.agentId}>{body}</div>
                );
              })}
              {nothingFound && !grouped ? (
                <p className="px-3 py-2 text-sm text-muted">No matching files.</p>
              ) : null}
            </div>
          </div>
        ) : null}

        <form
          className="flex w-full flex-col gap-1.5 rounded-2xl border border-line-strong bg-surface p-2 focus-within:border-accent"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          {attachments.length > 0 ? (
            <ul aria-label="Attachments" className="flex flex-wrap gap-1.5 px-1 pt-0.5">
              {attachments.map((a) => (
                <li key={`${a.agent_id}:${a.path}`} className="min-w-0 max-w-full">
                  <AttachmentChip
                    path={a.path}
                    agentName={
                      grouped ? mentionSources.find((s) => s.agentId === a.agent_id)?.agentName : undefined
                    }
                    onRemove={() => {
                      setAttachments(attachments.filter((x) => x !== a));
                      setNotice(null);
                      ref.current?.focus();
                    }}
                  />
                </li>
              ))}
            </ul>
          ) : null}
          <div className="flex items-end gap-2">
            <label htmlFor="composer-input" className="sr-only">
              Message
            </label>
            <textarea
              id="composer-input"
              ref={ref}
              rows={1}
              value={text}
              disabled={disabled}
              placeholder={disabled ? "Waiting for the reply to finish…" : placeholder}
              onChange={(e) => {
                const value = e.target.value;
                const pos = e.target.selectionStart ?? value.length;
                setText(value);
                setCaret(pos);
                // A closed mention stays closed while it is being typed; a new one opens.
                if (findMention(value, pos, anchor)?.start !== dismissed) setDismissed(null);
              }}
              onSelect={syncCaret}
              onKeyDown={onKeyDown}
              onBlur={() => {
                if (open) {
                  blurred.current = true;
                  setDismissed(mention.start);
                }
              }}
              onFocus={() => {
                if (blurred.current) setDismissed(null);
                blurred.current = false;
              }}
              aria-describedby="composer-help"
              aria-controls={open ? listId : undefined}
              aria-activedescendant={open && options.length > 0 ? options[activeIndex]?.id : undefined}
              aria-autocomplete={mentionSources.length > 0 ? "list" : undefined}
              className="max-h-52 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-[0.95rem] outline-none placeholder:text-muted/80 disabled:cursor-not-allowed"
            />
            {running ? (
              <Button variant="danger" onClick={onStop} disabled={stopping}>
                <StopIcon />
                {stopping ? "Stopping…" : "Stop"}
              </Button>
            ) : (
              <Button variant="primary" type="submit" disabled={!canSend} aria-label="Send">
                <SendIcon />
                <span className="hidden sm:inline">Send</span>
              </Button>
            )}
          </div>
        </form>
      </div>
      <div className="sr-only" role="status" aria-live="polite">
        {status}
      </div>
      <p id="composer-help" className="mx-auto mt-1.5 max-w-3xl px-1 text-xs text-muted">
        {tooLong ? (
          <span className="font-medium text-danger">
            Message is too long ({text.length.toLocaleString()} of {MESSAGE_MAX.toLocaleString()}{" "}
            characters).
          </span>
        ) : notice ? (
          <span className="font-medium text-warn">{notice}</span>
        ) : (
          <>
            Enter to send, Shift+Enter for a new line.
            {mentionSources.length > 0 ? " Type @ to attach a file." : null}
          </>
        )}
      </p>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
