import { useEffect } from "react";
import "./ShortcutsModal.css";

interface Props {
  open: boolean;
  onClose: () => void;
}

const isMac = typeof navigator !== "undefined" && /Mac|iP(hone|ad|od)/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

const groups: { title: string; items: { keys: string[]; label: string }[] }[] = [
  {
    title: "Navigation",
    items: [
      { keys: [MOD, "K"], label: "Search sessions" },
      { keys: [MOD, "B"], label: "Toggle sidebar" },
      { keys: [MOD, "Shift", "N"], label: "New chat" },
      { keys: ["Esc"], label: "Close dialog / drawer" },
    ],
  },
  {
    title: "Chat",
    items: [
      { keys: ["Enter"], label: "Send message" },
      { keys: ["Shift", "Enter"], label: "Newline in input" },
    ],
  },
  {
    title: "Help",
    items: [{ keys: [MOD, "/"], label: "Show this cheat sheet" }],
  },
];

export default function ShortcutsModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="shortcuts-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="shortcuts-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
      >
        <div className="shortcuts-modal-header">
          <h2>Keyboard shortcuts</h2>
          <button className="shortcuts-modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="shortcuts-modal-body">
          {groups.map((group) => (
            <section key={group.title} className="shortcuts-group">
              <h3>{group.title}</h3>
              <ul>
                {group.items.map((it) => (
                  <li key={it.label}>
                    <span className="shortcuts-label">{it.label}</span>
                    <span className="shortcuts-keys">
                      {it.keys.map((k, i) => (
                        <kbd key={i}>{k}</kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
