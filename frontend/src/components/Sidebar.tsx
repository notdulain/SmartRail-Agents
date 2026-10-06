import { useEffect, useRef } from "react";
import type { Agent, Conversation } from "../api/types";
import { providerName } from "../lib/models";
import { initials, speakerStyle } from "../lib/speakerColor";
import { useAppData } from "../state/appData";
import { Button } from "./ui/Button";
import {
  ChatIcon,
  CloseIcon,
  PencilIcon,
  PlusIcon,
  SettingsIcon,
  UsersIcon,
} from "./ui/icons";

interface Props {
  open: boolean;
  selectedId: string | null;
  onClose(): void;
  onSelect(id: string): void;
  onNewAgent(): void;
  onNewDiscussion(): void;
  onEditAgent(agent: Agent): void;
  onChatWithAgent(agent: Agent): void;
  onOpenSettings(): void;
}

export function Sidebar({
  open,
  selectedId,
  onClose,
  onSelect,
  onNewAgent,
  onNewDiscussion,
  onEditAgent,
  onChatWithAgent,
  onOpenSettings,
}: Props) {
  const { agents, conversations, providers, loaded, settings } = useAppData();
  const closeRef = useRef<HTMLButtonElement>(null);
  const active = agents.filter((a) => !a.archived);
  const archived = agents.filter((a) => a.archived);
  const sorted = [...conversations].sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  useEffect(() => {
    if (open && window.matchMedia?.("(max-width: 767px)").matches) closeRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {open ? (
        <div
          className="fixed inset-0 z-20 bg-(--overlay) md:hidden"
          aria-hidden="true"
          onClick={onClose}
        />
      ) : null}
      <aside
        aria-label="Sidebar"
        className={`fixed inset-y-0 left-0 z-30 flex w-72 max-w-[85vw] flex-col border-r border-line bg-sunken max-md:transition-transform max-md:duration-200 md:static md:max-w-none md:translate-x-0 md:visible ${
          open ? "translate-x-0" : "invisible -translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center justify-between px-4">
          <span className="text-[0.95rem] font-semibold tracking-tight">SmartRail Agents</span>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close sidebar"
            className="rounded-lg p-1.5 text-muted hover:bg-line hover:text-fg md:hidden"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="flex flex-col gap-2 px-3 pb-3">
          <Button variant="primary" onClick={onNewDiscussion} className="w-full justify-center">
            <PlusIcon />
            New discussion
          </Button>
          <Button onClick={onNewAgent} className="w-full justify-center">
            <PlusIcon />
            New agent
          </Button>
        </div>

        <nav aria-label="Conversations and agents" className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          <section aria-labelledby="sb-discussions">
            <h2 id="sb-discussions" className="px-2 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-muted">
              Discussions
            </h2>
            {!loaded ? (
              <p className="px-2 py-1 text-sm text-muted">Loading…</p>
            ) : sorted.length === 0 ? (
              <p className="px-2 py-1 text-sm text-muted">No discussions yet.</p>
            ) : (
              <ul className="space-y-0.5">
                {sorted.map((c) => (
                  <li key={c.id}>
                    <ConversationRow
                      conversation={c}
                      selected={c.id === selectedId}
                      onSelect={() => onSelect(c.id)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="sb-agents" className="mt-4">
            <h2 id="sb-agents" className="px-2 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-muted">
              Agents
            </h2>
            {!loaded ? (
              <p className="px-2 py-1 text-sm text-muted">Loading…</p>
            ) : active.length === 0 ? (
              <p className="px-2 py-1 text-sm text-muted">No agents yet. Create one to begin.</p>
            ) : (
              <ul className="space-y-0.5">
                {active.map((a) => (
                  <li key={a.id}>
                    <AgentRow
                      agent={a}
                      subtitle={`${providerName(providers, a.provider_id)} / ${a.model_id}`}
                      isCoordinator={settings?.coordinator_agent_id === a.id}
                      onChat={() => onChatWithAgent(a)}
                      onEdit={() => onEditAgent(a)}
                    />
                  </li>
                ))}
              </ul>
            )}
            {archived.length > 0 ? (
              <details className="mt-2">
                <summary className="cursor-pointer rounded-lg px-2 py-1 text-xs font-medium text-muted hover:bg-line">
                  Archived ({archived.length})
                </summary>
                <ul className="mt-1 space-y-0.5">
                  {archived.map((a) => (
                    <li key={a.id}>
                      <AgentRow
                        agent={a}
                        subtitle="Archived"
                        onEdit={() => onEditAgent(a)}
                        dimmed
                      />
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </section>
        </nav>

        <div className="border-t border-line p-3">
          <Button variant="ghost" onClick={onOpenSettings} className="w-full" disabled={!settings}>
            <SettingsIcon />
            Settings
          </Button>
        </div>
      </aside>
    </>
  );
}

function ConversationRow({
  conversation,
  selected,
  onSelect,
}: {
  conversation: Conversation;
  selected: boolean;
  onSelect(): void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? "page" : undefined}
      className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm ${
        selected ? "bg-surface font-medium shadow-sm ring-1 ring-line" : "hover:bg-line/60"
      }`}
    >
      {conversation.type === "group" ? (
        <UsersIcon className="shrink-0 text-muted" />
      ) : (
        <ChatIcon className="shrink-0 text-muted" />
      )}
      <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
      <span className="sr-only">{conversation.type === "group" ? "Group discussion" : "Direct chat"}</span>
    </button>
  );
}

function AgentRow({
  agent,
  subtitle,
  isCoordinator,
  dimmed,
  onChat,
  onEdit,
}: {
  agent: Agent;
  subtitle: string;
  isCoordinator?: boolean;
  dimmed?: boolean;
  onChat?(): void;
  onEdit(): void;
}) {
  const body = (
    <>
      <span
        aria-hidden="true"
        style={speakerStyle(agent.id)}
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-speaker-soft text-[0.68rem] font-semibold text-speaker"
      >
        {initials(agent.name)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate font-medium">{agent.name}</span>
          {isCoordinator ? (
            <span className="shrink-0 rounded-full bg-accent-soft px-1.5 text-[0.65rem] font-medium text-accent">
              Coordinator
            </span>
          ) : null}
        </span>
        <span className="block truncate text-xs text-muted">{subtitle}</span>
      </span>
    </>
  );
  return (
    <div className={`group flex items-center rounded-lg hover:bg-line/60 ${dimmed ? "opacity-70" : ""}`}>
      {onChat ? (
        <button
          type="button"
          onClick={onChat}
          title={`Chat with ${agent.name}`}
          aria-label={`Chat with ${agent.name}`}
          className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm"
        >
          {body}
        </button>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-2.5 px-2 py-1.5 text-sm">{body}</div>
      )}
      <button
        type="button"
        onClick={onEdit}
        aria-label={`Edit ${agent.name}`}
        title="Edit agent"
        className="mr-1 rounded-lg p-1.5 text-muted hover:bg-line hover:text-fg"
      >
        <PencilIcon width={16} height={16} />
      </button>
    </div>
  );
}

