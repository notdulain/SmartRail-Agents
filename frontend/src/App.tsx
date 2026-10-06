import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { Agent, RunEvent } from "./api/types";
import { AgentEditorDialog } from "./components/AgentEditorDialog";
import { ChatView } from "./components/ChatView";
import { NewDiscussionDialog } from "./components/NewDiscussionDialog";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { Toaster } from "./components/Toaster";
import { Button } from "./components/ui/Button";
import { AppDataProvider, useAppData } from "./state/appData";
import { ToastProvider, useToasts } from "./state/toast";

type DialogState =
  | { kind: "agent"; agent: Agent | null }
  | { kind: "discussion" }
  | { kind: "settings" }
  | null;

function readHash(): string | null {
  const m = /^#\/c\/(.+)$/.exec(window.location.hash);
  return m ? decodeURIComponent(m[1]) : null;
}

function Shell() {
  const toasts = useToasts();
  const data = useAppData();
  const [selectedId, setSelectedId] = useState<string | null>(readHash);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const onHash = () => setSelectedId(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // The drawer only exists on narrow screens; drop it when the window grows past that.
  useEffect(() => {
    const mq = window.matchMedia?.("(min-width: 768px)");
    if (!mq) return;
    const onChange = () => mq.matches && setSidebarOpen(false);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  const select = useCallback((id: string | null) => {
    window.location.hash = id ? `#/c/${encodeURIComponent(id)}` : "";
    setSelectedId(id);
    setSidebarOpen(false);
  }, []);

  const { refreshConversations, refreshSettings } = data;
  const onRunEnded = useCallback(
    (_event: RunEvent) => {
      void refreshConversations().catch(() => undefined);
      void refreshSettings();
    },
    [refreshConversations, refreshSettings],
  );

  async function chatWithAgent(agent: Agent) {
    const existing = data.conversations
      .filter((c) => c.type === "direct" && c.participant_ids[0] === agent.id)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
    if (existing) return select(existing.id);
    try {
      const c = await api.createConversation({ type: "direct", participant_ids: [agent.id] });
      await data.refreshConversations();
      select(c.id);
    } catch (err) {
      toasts.error(err);
    }
  }

  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const openDialog = (d: DialogState) => {
    setSidebarOpen(false);
    setDialog(d);
  };
  const openAgent = (agent: Agent | null) => openDialog({ kind: "agent", agent });

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar
        open={sidebarOpen}
        selectedId={selectedId}
        onClose={closeSidebar}
        onSelect={select}
        onNewAgent={() => openAgent(null)}
        onNewDiscussion={() => openDialog({ kind: "discussion" })}
        onEditAgent={openAgent}
        onChatWithAgent={(a) => void chatWithAgent(a)}
        onOpenSettings={() => openDialog({ kind: "settings" })}
      />
      <main inert={sidebarOpen || undefined} className="flex min-h-0 min-w-0 flex-1 flex-col">
        {data.loadFailed ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-sm text-muted" role="alert">
              Cannot reach the SmartRail backend. Make sure it is running, then try again.
            </p>
            <Button onClick={data.retry}>Try again</Button>
          </div>
        ) : (
          <ChatView
            key={selectedId ?? "home"}
            conversationId={selectedId}
            onOpenSidebar={() => setSidebarOpen(true)}
            onOpenSettings={() => openDialog({ kind: "settings" })}
            onEditAgent={openAgent}
            onNewDiscussion={() => openDialog({ kind: "discussion" })}
            onNewAgent={() => openAgent(null)}
            onRunEnded={onRunEnded}
          />
        )}
      </main>

      {dialog?.kind === "agent" ? (
        <AgentEditorDialog
          key={dialog.agent?.id ?? "new"}
          agent={dialog.agent}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "discussion" ? (
        <NewDiscussionDialog
          onClose={() => setDialog(null)}
          onCreated={(c) => {
            setDialog(null);
            select(c.id);
          }}
          onOpenSettings={() => openDialog({ kind: "settings" })}
          onNewAgent={() => openAgent(null)}
        />
      ) : null}
      {dialog?.kind === "settings" && data.settings ? (
        <SettingsDialog settings={data.settings} onClose={() => setDialog(null)} />
      ) : null}
      <Toaster />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppDataProvider>
        <Shell />
      </AppDataProvider>
    </ToastProvider>
  );
}
