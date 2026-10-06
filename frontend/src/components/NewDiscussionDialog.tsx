import { useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { Agent, Conversation, ConversationType, Settings } from "../api/types";
import { describeError } from "../lib/errors";
import { providerName } from "../lib/models";
import { useAppData } from "../state/appData";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { Field, inputClass } from "./ui/Field";

export const TOPIC_MAX = 2000;

export interface DiscussionForm {
  type: ConversationType;
  agentId: string;
  groupIds: string[];
  topic: string;
}

export interface DiscussionErrors {
  agent?: string;
  group?: string;
  topic?: string;
}

export function validateDiscussion(f: DiscussionForm): DiscussionErrors {
  const errors: DiscussionErrors = {};
  if (f.type === "direct") {
    if (!f.agentId) errors.agent = "Choose an agent to chat with.";
  } else {
    if (f.groupIds.length < 2) errors.group = "Choose at least two agents.";
    if (!f.topic.trim()) errors.topic = "Enter the topic to discuss.";
    else if (f.topic.length > TOPIC_MAX) errors.topic = `Topic is too long (max ${TOPIC_MAX} characters).`;
  }
  return errors;
}

/** True when a coordinator exists and is an active agent. */
export function hasCoordinator(settings: Settings | null, agents: Agent[]): boolean {
  const id = settings?.coordinator_agent_id;
  return !!id && agents.some((a) => a.id === id && !a.archived);
}

interface Props {
  onClose(): void;
  onCreated(c: Conversation): void;
  onOpenSettings(): void;
  onNewAgent(): void;
}

export function NewDiscussionDialog({ onClose, onCreated, onOpenSettings, onNewAgent }: Props) {
  const { agents, settings, providers, refreshConversations } = useAppData();
  const active = agents.filter((a) => !a.archived);

  const [type, setType] = useState<ConversationType>("direct");
  const [agentId, setAgentId] = useState("");
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [errors, setErrors] = useState<DiscussionErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const typeRef = useRef<HTMLInputElement>(null);
  const agentRef = useRef<HTMLSelectElement>(null);
  const groupRef = useRef<HTMLFieldSetElement>(null);
  const topicRef = useRef<HTMLTextAreaElement>(null);

  const coordinatorOk = hasCoordinator(settings, agents);

  function toggle(id: string) {
    setGroupIds((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
    setErrors((e) => ({ ...e, group: undefined }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setServerError(null);
    const found = validateDiscussion({ type, agentId, groupIds, topic });
    setErrors(found);
    if (found.agent) return agentRef.current?.focus();
    if (found.group) return groupRef.current?.querySelector<HTMLElement>("input")?.focus();
    if (found.topic) return topicRef.current?.focus();

    setSubmitting(true);
    try {
      const conversation = await api.createConversation(
        type === "direct"
          ? { type, participant_ids: [agentId] }
          : {
              type,
              // Keep the sidebar order of agents so speaking order is predictable.
              participant_ids: active.filter((a) => groupIds.includes(a.id)).map((a) => a.id),
              topic: topic.trim(),
            },
      );
      await refreshConversations();
      onCreated(conversation);
    } catch (err) {
      setServerError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New discussion"
      description="Chat with one agent, or start a group discussion."
      size="max-w-xl"
      initialFocus={typeRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="discussion-form"
            disabled={submitting || active.length === 0}
          >
            {submitting ? "Creating…" : type === "direct" ? "Start chat" : "Create discussion"}
          </Button>
        </>
      }
    >
      {active.length === 0 ? (
        <div className="space-y-3 py-4 text-center">
          <p className="text-sm text-muted">You need at least one agent before starting a chat.</p>
          <Button variant="primary" onClick={onNewAgent}>
            Create an agent
          </Button>
        </div>
      ) : (
        <form id="discussion-form" onSubmit={submit} noValidate className="space-y-4">
          {serverError ? (
            <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
              <strong className="font-semibold">{describeError(serverError).title}.</strong>{" "}
              {describeError(serverError).message}
            </div>
          ) : null}

          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium">Type</legend>
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ["direct", "Direct chat", "One agent"],
                  ["group", "Group discussion", "Two or more agents"],
                ] as const
              ).map(([value, label, sub]) => (
                <label
                  key={value}
                  className={`flex cursor-pointer flex-col rounded-lg border px-3 py-2 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent ${
                    type === value ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-sunken"
                  }`}
                >
                  <input
                    ref={value === "direct" ? typeRef : undefined}
                    type="radio"
                    name="discussion-type"
                    value={value}
                    checked={type === value}
                    onChange={() => {
                      setType(value);
                      setErrors({});
                    }}
                    className="sr-only"
                  />
                  <span className="font-medium">{label}</span>
                  <span className="text-xs text-muted">{sub}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {type === "direct" ? (
            <Field label="Agent" error={errors.agent}>
              {(p) => (
                <select
                  {...p}
                  ref={agentRef}
                  value={agentId}
                  onChange={(e) => {
                    setAgentId(e.target.value);
                    setErrors({});
                  }}
                  className={inputClass}
                >
                  <option value="">Choose an agent…</option>
                  {active.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({providerName(providers, a.provider_id)} / {a.model_id})
                    </option>
                  ))}
                </select>
              )}
            </Field>
          ) : (
            <>
              {!coordinatorOk ? (
                <div
                  role="note"
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn"
                >
                  <span>
                    Group discussions need a coordinator agent to set the agenda and summarise.
                  </span>
                  <Button size="sm" onClick={onOpenSettings}>
                    Open Settings
                  </Button>
                </div>
              ) : null}

              <fieldset ref={groupRef} className="space-y-1.5" aria-describedby="group-count">
                <legend className="text-sm font-medium">Participants</legend>
                <ul className="scroll-thin max-h-48 divide-y divide-line overflow-y-auto rounded-lg border border-line-strong">
                  {active.map((a) => (
                    <li key={a.id}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-sunken">
                        <input
                          type="checkbox"
                          checked={groupIds.includes(a.id)}
                          onChange={() => toggle(a.id)}
                          className="size-4 accent-(--accent)"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{a.name}</span>
                          <span className="block truncate text-xs text-muted">
                            {providerName(providers, a.provider_id)} / {a.model_id}
                          </span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
                <p id="group-count" className="text-xs text-muted">
                  {groupIds.length} selected. There is no limit on participants.
                </p>
                {errors.group ? (
                  <p className="text-xs font-medium text-danger" role="alert">
                    {errors.group}
                  </p>
                ) : null}
              </fieldset>

              <Field label="Topic" error={errors.topic} hint="What should the group discuss?">
                {(p) => (
                  <textarea
                    {...p}
                    ref={topicRef}
                    rows={3}
                    value={topic}
                    onChange={(e) => {
                      setTopic(e.target.value);
                      setErrors((x) => ({ ...x, topic: undefined }));
                    }}
                    className={`${inputClass} resize-y`}
                  />
                )}
              </Field>
            </>
          )}
        </form>
      )}
    </Dialog>
  );
}
