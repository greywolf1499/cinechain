import { useEffect, useId, useRef, useSyncExternalStore } from "react";

interface ModalEntry {
  id: string;
  onClose: () => void;
  onEscape?: () => boolean;
}

const stack: ModalEntry[] = [];
const subscribers = new Set<() => void>();
let originalBodyOverflow: string | null = null;

function notifySubscribers() {
  subscribers.forEach((subscriber) => subscriber());
}

function handleKeyDown(event: KeyboardEvent) {
  if (event.key !== "Escape") return;

  const topModal = stack.at(-1);
  if (!topModal) return;

  event.preventDefault();
  if (topModal.onEscape?.() !== true) topModal.onClose();
}

function registerModal(entry: ModalEntry) {
  if (stack.length === 0) {
    originalBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
  }

  stack.push(entry);
  notifySubscribers();

  return () => {
    const index = stack.findIndex((modal) => modal.id === entry.id);
    if (index === -1) return;

    stack.splice(index, 1);
    if (stack.length === 0) {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = originalBodyOverflow ?? "";
      originalBodyOverflow = null;
    }
    notifySubscribers();
  };
}

function subscribe(subscriber: () => void) {
  subscribers.add(subscriber);
  return () => subscribers.delete(subscriber);
}

function getTopModalId() {
  return stack.at(-1)?.id ?? null;
}

export function useModalStack(
  open: boolean,
  onClose: () => void,
  onEscape?: () => boolean,
) {
  const id = useId();
  const onCloseRef = useRef(onClose);
  const onEscapeRef = useRef(onEscape);

  useEffect(() => {
    onCloseRef.current = onClose;
    onEscapeRef.current = onEscape;
  }, [onClose, onEscape]);

  useEffect(() => {
    if (!open) return;
    return registerModal({
      id,
      onClose: () => onCloseRef.current(),
      onEscape: () => onEscapeRef.current?.() ?? false,
    });
  }, [id, open]);

  return useSyncExternalStore(
    subscribe,
    () => getTopModalId() === id,
    () => false,
  );
}
