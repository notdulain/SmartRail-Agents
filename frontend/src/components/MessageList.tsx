import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { FileRef, Message, ToolCall } from "../api/types";
import { needsAgentEdit } from "../lib/errors";
import { initials, speakerStyle } from "../lib/speakerColor";
import { stageLabel } from "../lib/stages";
import { Button } from "./ui/Button";
import { AttachmentChip } from "./AttachmentChip";
import { AlertIcon, CheckIcon, ChevronRightIcon, SpinnerIcon, ToolIcon } from "./ui/icons";

const STAGE_HINTS: Record<number, string> = {
  0: "Coordinator sets the agenda",
  1: "Everyone contributes",
  2: "Participants respond to each other",
  3: "Coordinator summarises",
};

interface Props {
  messages: Message[];
  onEditAgent?(agentId: string): void;
  /**
   * Names the agent whose folder an attachment came from (group chats, where files can come
   * from several participants). Omit to show paths only.
   */
  attachmentAgentName?(agentId: string): string | undefined;
}

export function MessageList({ messages, onEditAgent, attachmentAgentName }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const byId = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages]);

  // Keep the newest text and tool activity in view while streaming, unless the reader scrolled up.
  const last = messages[messages.length - 1];
  const lastTools = (last?.tool_calls ?? []).map((t) => t.status[0]).join("");
  const tail = `${messages.length}:${last?.content.length ?? 0}:${last?.status ?? ""}:${lastTools}`;
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [tail]);

  useEffect(() => {
    stick.current = true;
  }, []);

  const streaming = messages.find((m) => m.status === "streaming");

  let prevStageKey: string | null = null;
  return (
    <div
      ref={scroller}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="scroll-thin min-h-0 flex-1 overflow-y-auto"
    >
      <div className="sr-only" role="status" aria-live="polite">
        {streaming ? `${streaming.speaker_name} is replying` : ""}
      </div>
      <ol
        aria-label="Conversation"
        className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6"
      >
        {messages.map((m) => {
          let divider: ReactNode = null;
          if (m.role === "agent" && m.stage !== null) {
            const key = `${m.run_id ?? ""}:${m.stage}`;
            if (key !== prevStageKey) {
              divider = <StageDivider stage={m.stage} />;
              prevStageKey = key;
            }
          } else if (m.role === "user") {
            prevStageKey = null;
          }
          const replyTarget = m.reply_to_id ? byId.get(m.reply_to_id) : undefined;
          return (
            <li key={m.id} className="list-none">
              {divider}
              {m.role === "user" ? (
                <UserMessage message={m} agentName={attachmentAgentName} />
              ) : (
                <AgentMessage message={m} replyTo={replyTarget} onEditAgent={onEditAgent} />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function StageDivider({ stage }: { stage: number }) {
  return (
    <div
      role="separator"
      aria-label={stageLabel(stage) ?? undefined}
      className="mb-4 flex items-center gap-3 text-xs text-muted"
    >
      <span className="h-px flex-1 bg-line" />
      <span>
        <span className="font-semibold uppercase tracking-wide">{stageLabel(stage)}</span>
        {STAGE_HINTS[stage] ? <span className="hidden sm:inline"> · {STAGE_HINTS[stage]}</span> : null}
      </span>
      <span className="h-px flex-1 bg-line" />
    </div>
  );
}

function UserMessage({
  message,
  agentName,
}: {
  message: Message;
  agentName?(agentId: string): string | undefined;
}) {
  const attachments: FileRef[] = message.attachments ?? [];
  const images = message.images ?? [];
  return (
    <article className="flex flex-col items-end gap-1" aria-label={`${message.speaker_name} message`}>
      <div className="text-xs font-medium text-muted">{message.speaker_name}</div>
      {message.content ? (
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-md bg-accent-soft px-4 py-2.5 text-[0.95rem]">
          {message.content}
        </div>
      ) : null}
      {images.length > 0 ? (
        <ul aria-label="Attached images" className="flex max-w-[85%] flex-wrap justify-end gap-2">
          {images.map((image, index) => (
            <li key={index}>
              <img src={image.data_url} alt={image.filename} className="max-h-64 max-w-full rounded-xl object-contain" />
            </li>
          ))}
        </ul>
      ) : null}
      {attachments.length > 0 ? (
        <ul aria-label="Attached files" className="flex max-w-[85%] flex-wrap justify-end gap-1.5">
          {attachments.map((a) => (
            <li key={`${a.agent_id}:${a.path}`} className="min-w-0">
              <AttachmentChip path={a.path} agentName={agentName?.(a.agent_id)} />
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

const TOOL_VERBS: Record<string, string> = {
  read: "Reading",
  list: "Listing",
  glob: "Finding files",
  grep: "Searching",
  edit: "Editing",
  write: "Writing",
  patch: "Patching",
};

/** Compact record of the file tools an agent used: a summary row that expands to the list. */
export function ToolCalls({ calls, live }: { calls: ToolCall[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (calls.length === 0) return null;
  const running = calls.filter((c) => c.status === "running");
  const failed = calls.filter((c) => c.status === "error").length;
  const current = live ? running[running.length - 1] : undefined;
  const count = `${calls.length} ${calls.length === 1 ? "tool" : "tools"}`;

  let summary: ReactNode;
  if (current) {
    summary = (
      <>
        <span className="font-medium">{TOOL_VERBS[current.tool] ?? `Running ${current.tool}`}</span>{" "}
        <span className="min-w-0 truncate font-mono text-[0.75rem]">{current.title}</span>
      </>
    );
  } else {
    summary = (
      <span>
        Used {count}
        {failed > 0 ? <span className="text-danger"> · {failed} failed</span> : null}
      </span>
    );
  }

  return (
    <div className="mt-1 text-xs text-muted">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        className="flex max-w-full items-center gap-1.5 rounded-lg px-1.5 py-1 hover:bg-sunken hover:text-fg"
      >
        <ChevronRightIcon
          width={14}
          height={14}
          className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
        />
        {current ? (
          <SpinnerIcon width={14} height={14} className="shrink-0" />
        ) : (
          <ToolIcon width={14} height={14} className="shrink-0" />
        )}
        {summary}
        {current && calls.length > 1 ? <span className="shrink-0"> ({count})</span> : null}
      </button>
      {open ? (
        <ul id={listId} aria-label="Tool calls" className="ml-3 mt-0.5 space-y-0.5 border-l border-line pl-3">
          {calls.map((c) => (
            <li key={c.id} className="py-0.5">
              <div className="flex min-w-0 items-center gap-1.5">
                <ToolStatusIcon status={c.status} live={live} />
                <span className="shrink-0 font-mono text-fg">{c.tool}</span>
                <span aria-hidden="true">·</span>
                <span className="min-w-0 truncate font-mono" title={c.title}>
                  {c.title}
                </span>
              </div>
              {c.status === "error" && c.error ? (
                <p className="ml-5 mt-0.5 break-words text-danger">{c.error}</p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ToolStatusIcon({ status, live }: { status: ToolCall["status"]; live: boolean }) {
  if (status === "error") {
    return (
      <span className="shrink-0 text-danger">
        <AlertIcon width={14} height={14} />
        <span className="sr-only">Failed:</span>
      </span>
    );
  }
  if (status === "running" && live) {
    return (
      <span className="shrink-0">
        <SpinnerIcon width={14} height={14} />
        <span className="sr-only">Running:</span>
      </span>
    );
  }
  if (status === "running") {
    // The message finished without a result for this call (stopped or failed).
    return (
      <span className="shrink-0 text-muted">
        <ToolIcon width={14} height={14} />
        <span className="sr-only">Did not finish:</span>
      </span>
    );
  }
  return (
    <span className="shrink-0 text-ok">
      <CheckIcon width={14} height={14} />
      <span className="sr-only">Done:</span>
    </span>
  );
}

function AgentMessage({
  message,
  replyTo,
  onEditAgent,
}: {
  message: Message;
  replyTo?: Message;
  onEditAgent?(agentId: string): void;
}) {
  const key = message.agent?.agent_id ?? message.speaker_name;
  const provider = message.provider_id ?? message.agent?.provider_id;
  const model = message.model_id ?? message.agent?.model_id;
  const streaming = message.status === "streaming";
  const agentId = message.agent?.agent_id;
  const canEdit = message.status === "failed" && needsAgentEdit(message.error_code) && !!agentId;
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);

  async function copyResponse() {
    if (!message.content) return;
    try {
      await navigator.clipboard.writeText(message.content);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopyState("idle"), 2000);
  }

  return (
    <article
      style={speakerStyle(key)}
      aria-label={`${message.speaker_name} message`}
      className="flex gap-3"
    >
      <div
        aria-hidden="true"
        className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-speaker-soft text-xs font-semibold text-speaker"
      >
        {initials(message.speaker_name)}
      </div>
      <div className="min-w-0 flex-1">
        <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-semibold text-speaker">{message.speaker_name}</span>
          {provider && model ? (
            <span
              className="max-w-full truncate rounded-full border border-line bg-surface px-2 py-0.5 text-xs text-muted"
              title={`${provider} / ${model}`}
            >
              {provider} / {model}
            </span>
          ) : null}
          {message.status === "cancelled" ? (
            <span className="rounded-full bg-warn-soft px-2 py-0.5 text-xs font-medium text-warn">
              Stopped
            </span>
          ) : null}
          {message.status === "failed" ? (
            <span className="rounded-full bg-danger-soft px-2 py-0.5 text-xs font-medium text-danger">
              Failed
            </span>
          ) : null}
        </header>
        {replyTo ? (
          <p className="mt-0.5 text-xs text-muted">Replying to {replyTo.speaker_name}</p>
        ) : null}

        <ToolCalls calls={message.tool_calls ?? []} live={streaming} />

        {message.status === "failed" && !message.content ? null : message.content || !streaming ? (
          <div
            className={`mt-1 whitespace-pre-wrap break-words rounded-2xl rounded-tl-md bg-surface px-4 py-2.5 text-[0.95rem] ring-1 ring-line ${
              streaming ? "streaming-caret" : ""
            } ${message.status === "cancelled" ? "text-fg/80" : ""}`}
          >
            {message.content || <span className="text-muted">No text was produced.</span>}
          </div>
        ) : (
          <div
            className="mt-1 inline-block rounded-2xl rounded-tl-md bg-surface px-4 py-2.5 text-sm text-muted ring-1 ring-line"
            aria-label="Waiting for the first words"
          >
            <span className="streaming-caret">Thinking</span>
          </div>
        )}

        {message.content ? (
          <div className="mt-1 flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              onClick={copyResponse}
              aria-label={copyState === "idle" ? "Copy response" : copyState === "copied" ? "Copied response" : "Copy failed; try again"}
              className="text-xs text-muted"
            >
              {copyState === "idle" ? "Copy" : copyState === "copied" ? "Copied" : "Copy failed"}
            </Button>
          </div>
        ) : null}

        {message.status === "failed" ? (
          <div
            role="alert"
            className="mt-2 flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            <AlertIcon className="mt-0.5 shrink-0" />
            <p className="min-w-48 flex-1">
              {sentence(message.error ?? "This reply failed")}
              {message.error_code === "rate_limited"
                ? " The provider is rate limiting; try again shortly."
                : null}
              {message.error_code === "budget_exhausted"
                ? " Raise the budget in Settings to continue."
                : null}
            </p>
            {canEdit ? (
              <Button size="sm" onClick={() => agentId && onEditAgent?.(agentId)}>
                Edit agent
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}

/** Ends a message with a full stop so appended hints read naturally. */
function sentence(text: string): string {
  const t = text.trim();
  return /[.!?]$/.test(t) ? t : `${t}.`;
}
