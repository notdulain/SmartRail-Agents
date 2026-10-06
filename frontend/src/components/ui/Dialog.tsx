import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode, RefObject } from "react";
import { CloseIcon } from "./icons";

interface DialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Width class, e.g. "max-w-xl". */
  size?: string;
  /** Element to focus on open (defaults to the first focusable element). */
  initialFocus?: RefObject<HTMLElement | null>;
}

/** Modal with focus trap, Escape to close, labelled title and returned focus (via Radix). */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = "max-w-lg",
  initialFocus,
}: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-(--overlay)" />
        <RadixDialog.Content
          className={`animate-pop fixed left-1/2 top-1/2 z-50 flex max-h-[min(92dvh,46rem)] w-[calc(100vw-1.5rem)] ${size} -translate-x-1/2 -translate-y-1/2 flex-col rounded-2xl border border-line bg-surface shadow-xl`}
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={(e) => {
            if (initialFocus?.current) {
              e.preventDefault();
              initialFocus.current.focus();
            }
          }}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="min-w-0">
              <RadixDialog.Title className="text-base font-semibold">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="mt-0.5 text-sm text-muted">
                  {description}
                </RadixDialog.Description>
              ) : null}
            </div>
            <RadixDialog.Close
              aria-label="Close"
              className="-mr-1.5 -mt-1 rounded-lg p-1.5 text-muted hover:bg-sunken hover:text-fg"
            >
              <CloseIcon />
            </RadixDialog.Close>
          </div>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer ? (
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">
              {footer}
            </div>
          ) : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
