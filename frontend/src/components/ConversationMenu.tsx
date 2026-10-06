import * as Menu from "@radix-ui/react-dropdown-menu";
import { useState } from "react";
import { api } from "../api/client";
import { saveBlob } from "../lib/download";
import { useToasts } from "../state/toast";
import { DownloadIcon, MoreIcon } from "./ui/icons";

export function ConversationMenu({ conversationId }: { conversationId: string }) {
  const toasts = useToasts();
  const [busy, setBusy] = useState(false);

  async function exportTranscript() {
    setBusy(true);
    try {
      const { filename, blob } = await api.exportConversation(conversationId);
      saveBlob(blob, filename);
      toasts.info("Transcript exported", filename);
    } catch (err) {
      toasts.error(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label="Conversation menu"
        disabled={busy}
        className="rounded-lg p-2 text-muted hover:bg-sunken hover:text-fg disabled:opacity-50"
      >
        <MoreIcon />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={6}
          className="animate-pop z-50 min-w-48 rounded-xl border border-line bg-surface p-1 shadow-lg"
        >
          <Menu.Item
            onSelect={() => void exportTranscript()}
            className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm outline-none data-highlighted:bg-sunken"
          >
            <DownloadIcon />
            Export transcript
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
