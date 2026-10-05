import { useEffect, useId, useLayoutEffect, useRef, useState, type ElementType, type ReactNode } from "react";
import Modal from "../Modal";

const CLAMP = {
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
  5: "line-clamp-5",
  6: "line-clamp-6",
} as const;

type ExpandableTextProps = {
  text: string | null | undefined;
  lines: keyof typeof CLAMP;
  fallback?: ReactNode;
  className?: string;
  as?: ElementType;
  lead?: ReactNode;
  moreLabel?: string;
  lessLabel?: string;
  expandMode?: "inline" | "dialog";
  dialogTitle?: string;
};

export default function ExpandableText({
  text,
  lines,
  fallback = null,
  className = "",
  as: Element = "p",
  lead,
  moreLabel = "Read more",
  lessLabel = "Show less",
  expandMode = "inline",
  dialogTitle = "More details",
}: ExpandableTextProps) {
  const id = useId();
  const textRef = useRef<HTMLElement>(null);
  const [hasOverflow, setHasOverflow] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const content = text?.trim() ? text : fallback;

  useEffect(() => {
    setExpanded(false);
    setDialogOpen(false);
  }, [content, lines]);

  useLayoutEffect(() => {
    const element = textRef.current;
    if (!element || expanded) return;

    const measure = () => {
      setHasOverflow(element.scrollHeight > element.clientHeight + 1);
    };

    measure();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [content, expanded, expandMode, lines]);

  const isExpanded = expandMode === "dialog" ? dialogOpen : expanded;
  const clampClass =
    expandMode === "inline" && expanded ? "" : CLAMP[lines];

  return (
    <>
      <Element
        ref={textRef}
        id={expandMode === "dialog" && dialogOpen ? undefined : id}
        className={`${clampClass} break-words ${className}`.trim()}
      >
        {lead && <span className="font-medium">{lead} </span>}
        {content}
      </Element>
      {hasOverflow && (
        <button
          type="button"
          aria-expanded={isExpanded}
          aria-controls={id}
          onClick={() =>
            expandMode === "dialog" ? setDialogOpen(true) : setExpanded((value) => !value)
          }
          className="mt-1 inline-flex min-h-8 items-center rounded px-2 text-xs font-medium text-accent hover:text-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {isExpanded ? lessLabel : moreLabel}
        </button>
      )}
      {expandMode === "dialog" && (
        <Modal
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          title={dialogTitle}
        >
          <p id={id} className="whitespace-pre-wrap break-words text-sm text-zinc-300">
            {lead && <span className="font-medium">{lead} </span>}
            {content}
          </p>
        </Modal>
      )}
    </>
  );
}
