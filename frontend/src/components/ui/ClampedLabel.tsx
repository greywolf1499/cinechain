import type { ComponentPropsWithoutRef } from "react";

const CLAMP = {
  1: "line-clamp-1",
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
  5: "line-clamp-5",
  6: "line-clamp-6",
} as const;

type ClampedLabelProps = {
  text: string;
  lines: keyof typeof CLAMP;
  as?: "p" | "span" | "div";
  className?: string;
} & Omit<ComponentPropsWithoutRef<"p">, "children" | "className" | "title">;

export default function ClampedLabel({
  text,
  lines,
  as: Element = "span",
  className = "",
  ...props
}: ClampedLabelProps) {
  return (
    <Element
      {...props}
      title={text}
      className={`${CLAMP[lines]} break-words ${className}`.trim()}
    >
      {text}
    </Element>
  );
}
