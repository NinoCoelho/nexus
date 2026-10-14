/**
 * DeleteBotModal — removes a bot and decides what happens to its chats:
 * archive (default, restorable), delete permanently, or keep as plain chats.
 * Rendered on the modal stylesheet's backdrop/dialog primitives.
 */

import { useState } from "react";
import { deleteBot, type Bot, type DeleteBotChats } from "../../api";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  bot: Bot | null;
  onClose: () => void;
  onDeleted: () => void;
}

const OPTIONS: { value: DeleteBotChats; label: string; hint: string }[] = [
  { value: "archive", label: "Archive its chats", hint: "restorable from the main bot's archived view" },
  { value: "delete", label: "Delete its chats", hint: "messages removed permanently" },
  { value: "keep", label: "Keep its chats", hint: "they survive as ordinary main chats" },
];

export default function DeleteBotModal({ bot, onClose, onDeleted }: Props) {
  const toast = useToast();
  const [chats, setChats] = useState<DeleteBotChats>("archive");
  const [busy, setBusy] = useState(false);

  if (!bot) return null;

  const handleDelete = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await deleteBot(bot.id, chats);
      toast.success(`Deleted ${bot.name}`, {
        detail:
          result.sessions_deleted > 0
            ? `${result.sessions_deleted} ${result.sessions_deleted === 1 ? "chat" : "chats"} deleted`
            : result.sessions_archived > 0
              ? `${result.sessions_archived} ${result.sessions_archived === 1 ? "chat" : "chats"} archived`
              : undefined,
      });
      onDeleted();
      onClose();
    } catch (e) {
      toast.error("Couldn't delete the bot", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-dialog delete-bot-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Delete {bot.name}?</div>
        <div className="modal-message">
          The bot, its persona and its vault folder entry are removed. What should happen to its chats?
        </div>
        <div className="delete-bot-options">
          {OPTIONS.map((opt) => (
            <label key={opt.value} className={`delete-bot-option${chats === opt.value ? " delete-bot-option--on" : ""}`}>
              <input
                type="radio"
                name="delete-bot-chats"
                checked={chats === opt.value}
                onChange={() => setChats(opt.value)}
              />
              <span>
                <strong>{opt.label}</strong>
                <em>{opt.hint}</em>
              </span>
            </label>
          ))}
        </div>
        <div className="team-actions">
          <button className="team-cancel" onClick={onClose}>Cancel</button>
          <button className="delete-bot-confirm" disabled={busy} onClick={() => void handleDelete()}>
            {busy ? "Deleting…" : "Delete bot"}
          </button>
        </div>
      </div>
    </div>
  );
}
