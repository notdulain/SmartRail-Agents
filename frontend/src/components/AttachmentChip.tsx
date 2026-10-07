import { CloseIcon, FileIcon } from "./ui/icons";

interface Props {
  /** Relative path inside the agent's working directory ("/" separators). */
  path: string;
  /** Owner of the folder, shown in group chats. */
  agentName?: string;
  onRemove?(): void;
}

/** A file attached with `@`: file name first, the folder after it in muted text. */
export function AttachmentChip({ path, agentName, onRemove }: Props) {
  const cut = path.lastIndexOf("/");
  const dir = cut === -1 ? "" : path.slice(0, cut + 1);
  const name = cut === -1 ? path : path.slice(cut + 1);
  const full = agentName ? `${agentName}: ${path}` : path;
  return (
    <span
      title={full}
      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-surface py-0.5 pl-2 pr-1 text-xs"
    >
      <FileIcon width={13} height={13} className="shrink-0 text-muted" />
      {agentName ? <span className="shrink-0 font-medium text-muted">{agentName}</span> : null}
      <span className="flex min-w-0 font-mono">
        {dir ? <span className="min-w-0 truncate text-muted">{dir}</span> : null}
        <span className="shrink-0">{name}</span>
      </span>
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${path}`}
          className="shrink-0 rounded p-0.5 text-muted hover:bg-sunken hover:text-fg"
        >
          <CloseIcon width={12} height={12} />
        </button>
      ) : (
        <span className="w-1" />
      )}
    </span>
  );
}
