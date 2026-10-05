import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

type PopoverProps = {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  placement?: "bottom" | "top";
  label?: string;
  className?: string;
};

type Position = {
  left: number;
  top: number;
  width: number;
};

export default function Popover({
  anchorRef,
  open,
  onClose,
  children,
  placement = "bottom",
  label,
  className = "",
}: PopoverProps) {
  const id = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const [position, setPosition] = useState<Position | null>(null);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useLayoutEffect(() => {
    if (!open) {
      setPosition((current) => (current ? null : current));
      return;
    }

    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;

    const reposition = () => {
      const anchorRect = anchor.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const maxWidth = Math.min(window.innerWidth * 0.9, 384);
      const width = Math.min(anchorRect.width, maxWidth);
      const left = Math.min(
        Math.max(anchorRect.left, 8),
        Math.max(8, window.innerWidth - width - 8),
      );
      const gap = 4;
      const belowSpace = window.innerHeight - anchorRect.bottom - gap - 8;
      const aboveSpace = anchorRect.top - gap - 8;
      const flip =
        placement === "bottom"
          ? panelRect.height > belowSpace && aboveSpace > belowSpace
          : panelRect.height <= aboveSpace || belowSpace <= aboveSpace;
      const preferredTop = flip
        ? anchorRect.top - panelRect.height - gap
        : anchorRect.bottom + gap;
      const top = Math.min(
        Math.max(preferredTop, 8),
        Math.max(8, window.innerHeight - panelRect.height - 8),
      );

      setPosition((current) =>
        current?.left === left && current.top === top && current.width === width
          ? current
          : { left, top, width },
      );
    };

    reposition();
    const resizeObserver =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reposition);
    resizeObserver?.observe(anchor);
    resizeObserver?.observe(panel);
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (
        target instanceof Node &&
        !anchor.contains(target) &&
        !panel.contains(target)
      ) {
        onCloseRef.current();
      }
    };
    const onFocusOut = (event: FocusEvent) => {
      const nextTarget = event.relatedTarget;
      if (
        nextTarget instanceof Node &&
        (anchor.contains(nextTarget) || panel.contains(nextTarget))
      ) {
        return;
      }
      onCloseRef.current();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onCloseRef.current();
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("focusout", onFocusOut, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("focusout", onFocusOut, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [anchorRef, open, placement]);

  if (!open) return null;

  return createPortal(
    <div
      ref={panelRef}
      id={id}
      role="dialog"
      aria-label={label}
      tabIndex={-1}
      style={{
        position: "fixed",
        left: position?.left ?? 8,
        top: position?.top ?? 8,
        width: position?.width,
        visibility: position ? "visible" : "hidden",
      }}
      className={`z-[60] max-h-[50vh] max-w-[min(90vw,24rem)] overflow-y-auto rounded-md border border-app-border bg-app-surface shadow-xl ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}
