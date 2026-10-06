import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "./ui/Button";
import { SendIcon, StopIcon } from "./ui/icons";

export const MESSAGE_MAX = 20000;

interface Props {
  placeholder: string;
  /** A run is active or a message is being sent: the input is locked. */
  disabled: boolean;
  /** A run is active: show Stop instead of Send. */
  running: boolean;
  stopping: boolean;
  onSend(text: string): Promise<boolean>;
  onStop(): void;
}

export function Composer({ placeholder, disabled, running, stopping, onSend, onStop }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const wasDisabled = useRef(disabled);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  // Give focus back after a run finishes (the browser drops it when the field is disabled).
  useEffect(() => {
    if (wasDisabled.current && !disabled && document.activeElement === document.body) {
      ref.current?.focus();
    }
    wasDisabled.current = disabled;
  }, [disabled]);

  const trimmed = text.trim();
  const tooLong = text.length > MESSAGE_MAX;
  const canSend = !disabled && trimmed.length > 0 && !tooLong;

  async function submit() {
    if (!canSend) return;
    const ok = await onSend(text);
    if (ok) setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <div className="border-t border-line bg-bg px-4 pb-4 pt-3 sm:px-6">
      <form
        className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-2xl border border-line-strong bg-surface p-2 focus-within:border-accent"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
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
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          aria-describedby="composer-help"
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
      </form>
      <p id="composer-help" className="mx-auto mt-1.5 max-w-3xl px-1 text-xs text-muted">
        {tooLong ? (
          <span className="font-medium text-danger">
            Message is too long ({text.length.toLocaleString()} of {MESSAGE_MAX.toLocaleString()}{" "}
            characters).
          </span>
        ) : (
          <>Enter to send, Shift+Enter for a new line.</>
        )}
      </p>
    </div>
  );
}
