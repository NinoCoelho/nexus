import { useEffect } from "react";

export interface ShortcutHandlers {
  onShowHelp?: () => void;
  onFocusSearch?: () => void;
  onToggleSidebar?: () => void;
  onNewChat?: () => void;
  onFindInChat?: () => void;
  onEscape?: () => void;
}

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  return false;
}

export function useShortcuts(handlers: ShortcutHandlers) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;

      if (e.key === "Escape" && handlers.onEscape) {
        handlers.onEscape();
        return;
      }

      if (!mod) return;

      // "/" — Cmd+/ opens shortcut help. Allow even if focus is in input.
      if (e.key === "/" && handlers.onShowHelp) {
        e.preventDefault();
        handlers.onShowHelp();
        return;
      }

      // Skip the rest when typing in inputs unless explicitly safe.
      const inEditable = isEditableTarget(e.target);

      if (e.key.toLowerCase() === "k" && handlers.onFocusSearch) {
        e.preventDefault();
        handlers.onFocusSearch();
        return;
      }

      if (e.key.toLowerCase() === "b" && handlers.onToggleSidebar) {
        if (inEditable) return;
        e.preventDefault();
        handlers.onToggleSidebar();
        return;
      }

      // Cmd+Shift+N — new chat (Cmd+N is reserved by browser).
      if (e.shiftKey && e.key.toLowerCase() === "n" && handlers.onNewChat) {
        e.preventDefault();
        handlers.onNewChat();
        return;
      }

      // Cmd+Shift+F — find in current chat.
      if (e.shiftKey && e.key.toLowerCase() === "f" && handlers.onFindInChat) {
        e.preventDefault();
        handlers.onFindInChat();
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers]);
}
