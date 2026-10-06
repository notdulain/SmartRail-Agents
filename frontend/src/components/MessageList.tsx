import { useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import type { Message } from "../api/types";
import { needsAgentEdit } from "../lib/errors";
import { initials, speakerStyle } from "../lib/speakerColor";
import { stageLabel } from "../lib/stages";
import { Button } from "./ui/Button";
import { AlertIcon } from "./ui/icons";

const STAGE_HINTS: Record<number, string> = {
  0: "Coordinator sets the agenda",
  1: "Everyone contributes",
  2: "Participants respond to each other",
  3: "Coordinator summarises",
};

interface Props {
  messages: Message[];
  onEditAgent?(agentId: string): void;
}

export function MessageList({ messages, onEditAgent }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const byId = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages]);

  // Keep the newest text in view while streaming, unless the reader scrolled up.
  const last = messages[messages.length - 1];
  const tail = `${messages.length}:${last?.content.length ?? 0}:${last?.status ?? ""}`;
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
                <UserMessage message={m} />
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

function UserMessage({ message }: { message: Message }) {
  return (
    <article className="flex flex-col items-end gap-1" aria-label={`${message.speaker_name} message`}>
      <div className="text-xs font-medium text-muted">{message.speaker_name}</div>
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-md bg-accent-soft px-4 py-2.5 text-[0.95rem]">
        {message.content}
      </div>
    </article>
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

        {message.content || !streaming ? (
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

        {message.status === "failed" ? (
          <div
            role="alert"
            className="mt-2 flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            <AlertIcon className="mt-0.5 shrink-0" />
            <p className="min-w-0 flex-1">
              {message.error ?? "This reply failed."}
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
