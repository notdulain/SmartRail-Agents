import type { ReactNode } from "react";
import type { Agent, Conversation, RunEvent } from "../api/types";
import { isApiCode } from "../lib/errors";
import { providerName } from "../lib/models";
import { useAppData } from "../state/appData";
import { pausedBanner } from "../state/conversationState";
import { useToasts } from "../state/toast";
import { useConversation } from "../state/useConversation";
import { Composer, type MentionSource } from "./Composer";
import { ConversationMenu } from "./ConversationMenu";
import { MessageList } from "./MessageList";
import { hasCoordinator } from "./NewDiscussionDialog";
import { Button } from "./ui/Button";
import { ChatIcon, MenuIcon, UsersIcon } from "./ui/icons";

interface Props {
  conversationId: string | null;
  onOpenSidebar(): void;
  onOpenSettings(): void;
  onEditAgent(agent: Agent): void;
  onNewDiscussion(): void;
  onNewAgent(): void;
  onRunEnded(event: RunEvent): void;
}

export function ChatView({
  conversationId,
  onOpenSidebar,
  onOpenSettings,
  onEditAgent,
  onNewDiscussion,
  onNewAgent,
  onRunEnded,
}: Props) {
  const toasts = useToasts();
  const { agents, settings, loaded } = useAppData();
  const chat = useConversation(conversationId, {
    onRunEnded,
    onError: (err) => {
      // run_active is handled inline (we attach to the running reply); others become toasts.
      if (!isApiCode(err, "run_active")) toasts.error(err);
    },
  });
  const { conversation, state } = chat;

  // Mobile header needs a way to open the drawer even with no conversation.
  const header = (
    <header className="flex min-h-14 items-center gap-2 border-b border-line bg-surface px-3 sm:px-5">
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={onOpenSidebar}
        aria-label="Open sidebar"
      >
        <MenuIcon />
      </Button>
      {conversation ? (
        <>
          <div className="min-w-0 flex-1 py-2">
            <h1 className="truncate text-base font-semibold">{conversation.title}</h1>
            <ParticipantLine conversation={conversation} agents={agents} />
          </div>
          <ConversationMenu conversationId={conversation.id} />
        </>
      ) : (
        <h1 className="flex-1 truncate text-base font-semibold">SmartRail Agents</h1>
      )}
    </header>
  );

  if (!conversationId) {
    return (
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {header}
        <EmptyHome
          loaded={loaded}
          hasAgents={agents.some((a) => !a.archived)}
          onNewDiscussion={onNewDiscussion}
          onNewAgent={onNewAgent}
        />
      </div>
    );
  }

  const paused = pausedBanner(state);
  const isGroup = conversation?.type === "group";
  const running = !!state.activeRunId;
  const locked = running || chat.sending;
  const direct = conversation && conversation.type === "direct"
    ? agents.find((a) => a.id === conversation.participant_ids[0])
    : undefined;
  // Participants whose working directory can be searched with @.
  const mentionSources: MentionSource[] = (conversation?.participant_ids ?? []).flatMap((id) => {
    const a = agents.find((x) => x.id === id);
    return a?.working_directory ? [{ agentId: a.id, agentName: a.name }] : [];
  });
  const placeholder = isGroup
    ? "Message the group. Each message starts one bounded round of discussion."
    : `Message ${direct?.name ?? "the agent"}`;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {header}

      {chat.loadError ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <p className="text-sm text-muted" role="alert">
            {chat.loadError.code === "not_found"
              ? "This conversation no longer exists."
              : `Could not load the conversation. ${chat.loadError.message}`}
          </p>
          {chat.loadError.code !== "not_found" ? (
            <Button onClick={() => void chat.reload()}>Try again</Button>
          ) : null}
        </div>
      ) : chat.loading && !conversation ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted" role="status">
          Loading conversation…
        </div>
      ) : (
        <>
          {state.messages.length === 0 && conversation ? (
            <EmptyConversation
              conversation={conversation}
              disabled={locked}
              onStart={() => conversation.topic && void chat.send(conversation.topic)}
            />
          ) : (
            <MessageList
              messages={state.messages}
              attachmentAgentName={
                isGroup ? (id) => agents.find((a) => a.id === id)?.name : undefined
              }
              onEditAgent={(id) => {
                const agent = agents.find((a) => a.id === id);
                if (agent) onEditAgent(agent);
                else toasts.info("Agent not found", "It may have been deleted.");
              }}
            />
          )}

          <div className="space-y-2 px-4 sm:px-6">
            {isGroup && settings && !hasCoordinator(settings, agents) ? (
              <Banner tone="warn">
                No coordinator is set, so this discussion cannot run yet.
                <Button size="sm" onClick={onOpenSettings}>
                  Open Settings
                </Button>
              </Banner>
            ) : null}
            {paused ? (
              <Banner tone="warn" role="alert">
                <span>
                  <strong className="font-semibold">Run paused.</strong> {paused}
                </span>
                <Button size="sm" onClick={onOpenSettings}>
                  Open Settings
                </Button>
              </Banner>
            ) : null}
            {chat.connection === "reconnecting" ? (
              <Banner tone="info" role="status">
                Connection dropped. Reconnecting and resuming the reply…
              </Banner>
            ) : null}
            {chat.streamLost ? (
              <Banner tone="warn" role="alert">
                Lost the live connection to this reply.
                <Button size="sm" onClick={() => void chat.reload()}>
                  Reconnect
                </Button>
              </Banner>
            ) : null}
            {chat.sendError && chat.sendError.code === "run_active" ? (
              <Banner tone="info" role="status">
                A reply is already being generated here. Showing it live.
              </Banner>
            ) : null}
          </div>

          <Composer
            key={conversationId}
            conversationId={conversationId}
            placeholder={placeholder}
            mentionSources={mentionSources}
            labelSources={isGroup}
            disabled={locked || !conversation}
            running={running}
            stopping={chat.stopping}
            onSend={chat.send}
            onStop={() => void chat.stop()}
          />
        </>
      )}
    </div>
  );
}

function ParticipantLine({ conversation, agents }: { conversation: Conversation; agents: Agent[] }) {
  const { providers } = useAppData();
  const people = conversation.participant_ids.map((id) => agents.find((a) => a.id === id));
  if (conversation.type === "direct") {
    const a = people[0];
    return (
      <p className="truncate text-xs text-muted">
        {a ? `${a.name} · ${providerName(providers, a.provider_id)} / ${a.model_id}` : "Direct chat"}
      </p>
    );
  }
  const names = people.map((a, i) => a?.name ?? `Agent ${i + 1}`);
  return (
    <p className="truncate text-xs text-muted">
      <UsersIcon className="mr-1 inline -translate-y-px" width={14} height={14} />
      {names.length} participants: {names.join(", ")}
    </p>
  );
}

function Banner({
  tone,
  role,
  children,
}: {
  tone: "warn" | "info";
  role?: "alert" | "status";
  children: ReactNode;
}) {
  return (
    <div
      role={role}
      className={`mx-auto flex w-full max-w-3xl flex-wrap items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm ${
        tone === "warn" ? "bg-warn-soft text-warn" : "bg-accent-soft text-fg"
      }`}
    >
      {children}
    </div>
  );
}

function EmptyConversation({
  conversation,
  disabled,
  onStart,
}: {
  conversation: Conversation;
  disabled: boolean;
  onStart(): void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
      <ChatIcon width={28} height={28} className="text-muted" />
      {conversation.type === "group" ? (
        <>
          <p className="max-w-md text-sm text-muted">
            Topic: <span className="font-medium text-fg">{conversation.topic}</span>
          </p>
          <p className="max-w-md text-sm text-muted">
            Nothing runs until you send a message. Start with the topic, or write your own opening.
          </p>
          <Button variant="primary" onClick={onStart} disabled={disabled}>
            Start discussion
          </Button>
        </>
      ) : (
        <p className="text-sm text-muted">No messages yet. Say hello below.</p>
      )}
    </div>
  );
}

function EmptyHome({
  loaded,
  hasAgents,
  onNewDiscussion,
  onNewAgent,
}: {
  loaded: boolean;
  hasAgents: boolean;
  onNewDiscussion(): void;
  onNewAgent(): void;
}) {
  if (!loaded) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted" role="status">
        Loading…
      </div>
    );
  }
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
      <ChatIcon width={32} height={32} className="text-muted" />
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">
          {hasAgents ? "Pick a conversation" : "Create your first agent"}
        </h2>
        <p className="max-w-sm text-sm text-muted">
          {hasAgents
            ? "Choose one from the sidebar, or start a new chat or group discussion."
            : "Agents are roles backed by a model. Start from a railway template or write your own."}
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {hasAgents ? (
          <Button variant="primary" onClick={onNewDiscussion}>
            New discussion
          </Button>
        ) : null}
        <Button variant={hasAgents ? "secondary" : "primary"} onClick={onNewAgent}>
          New agent
        </Button>
      </div>
    </div>
  );
}
