import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { createPortal } from "react-dom";
import { useModalStack } from "./modalStack";

export default function Modal({
  open,
  onClose,
  title,
  children,
  widthClassName = "max-w-md",
  footer,
  bodyClassName = "max-h-[70vh] overflow-y-auto px-5 py-4",
  onEscape,
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  widthClassName?: string;
  footer?: ReactNode;
  bodyClassName?: string;
  onEscape?: () => boolean;
  dismissible?: boolean;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const isTopModal = useModalStack(open, onClose, dismissible ? onEscape : () => true);

  useEffect(() => {
    if (!open) return;

    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = panelRef.current;
    const firstFocusable = bodyRef.current?.querySelector<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    (firstFocusable?.getClientRects().length ? firstFocusable : panel)?.focus();

    return () => {
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [open]);

  if (!open) return null;

  function trapTabKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Tab" || !isTopModal) return;

    const panel = panelRef.current;
    if (!panel) return;

    const focusable = Array.from(
      panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => element.getClientRects().length > 0);
    if (focusable.length === 0) {
      event.preventDefault();
      panel.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={trapTabKey}
        className={`w-full ${widthClassName} rounded-xl border border-app-border bg-app-surface shadow-2xl shadow-black/50`}
      >
        <div className="flex items-center justify-between border-b border-app-border px-5 py-3.5">
          <h2 id={titleId} className="text-sm font-semibold text-zinc-100">{title}</h2>
          {dismissible && <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-zinc-500 transition-colors hover:bg-app-surface-hover hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <X className="h-4 w-4" />
          </button>}
        </div>
        <div ref={bodyRef} className={bodyClassName}>{children}</div>
        {footer != null && (
          <div className="border-t border-app-border px-5 py-3.5">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  );
}
