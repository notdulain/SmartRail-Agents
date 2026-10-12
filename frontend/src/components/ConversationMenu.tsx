import * as Menu from "@radix-ui/react-dropdown-menu";
import { useState } from "react";
import { api } from "../api/client";
import { saveBlob } from "../lib/download";
import { useAppData } from "../state/appData";
import { useToasts } from "../state/toast";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { DownloadIcon, MoreIcon, TrashIcon } from "./ui/icons";

interface Props {
  conversationId: string;
  title?: string;
  /** Deleting is refused while a reply is being generated. */
  deleteDisabled?: boolean;
  onDeleted?(): void;
}

export function ConversationMenu({ conversationId, title, deleteDisabled, onDeleted }: Props) {
  const toasts = useToasts();
  const { refreshConversations } = useAppData();
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

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

  async function deleteConversation() {
    setBusy(true);
    try {
      await api.deleteConversation(conversationId);
    } catch (err) {
      toasts.error(err);
      setBusy(false);
      setConfirmDelete(false);
      return;
    }
    toasts.info("Discussion deleted", title);
    setConfirmDelete(false);
    await refreshConversations().catch(() => undefined);
    onDeleted?.();
  }

  const itemClass =
    "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm outline-none data-highlighted:bg-sunken data-disabled:cursor-not-allowed data-disabled:opacity-50";

  return (
    <>
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
            <Menu.Item onSelect={() => void exportTranscript()} className={itemClass}>
              <DownloadIcon />
              Export transcript
            </Menu.Item>
            <Menu.Item
              disabled={deleteDisabled}
              onSelect={() => setConfirmDelete(true)}
              className={`${itemClass} text-danger data-highlighted:bg-danger-soft`}
            >
              <TrashIcon />
              Delete discussion
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>

      <Dialog
        open={confirmDelete}
        onOpenChange={(open) => !busy && setConfirmDelete(open)}
        title="Delete this discussion?"
        description={
          title ? `“${title}” and all of its messages will be permanently deleted.` : undefined
        }
        size="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void deleteConversation()} disabled={busy}>
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted">
          This cannot be undone. Export the transcript first if you want to keep a copy.
        </p>
      </Dialog>
    </>
  );
}
