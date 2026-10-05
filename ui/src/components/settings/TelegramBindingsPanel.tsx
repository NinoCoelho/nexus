/**
 * TelegramBindingsPanel — topic/chat management table. Lists every Telegram
 * binding (DM / group / topic → project → active chat) with project rebind
 * and unbind actions. Lives inside the Settings Telegram section.
 */

import { useCallback, useEffect, useState } from "react";
import {
  deleteTelegramBinding,
  getTelegramBindings,
  patchTelegramBinding,
  type TelegramBindingInfo,
} from "../../api/telegram";
import { listProjects, type ProjectSummary } from "../../api/projects";
import { useToast } from "../../toast/ToastProvider";
import "./TelegramBindingsPanel.css";

const KIND_ICON: Record<TelegramBindingInfo["kind"], string> = {
  dm: "✉️",
  group: "👥",
  topic: "🧵",
};

export default function TelegramBindingsPanel() {
  const toast = useToast();
  const [bindings, setBindings] = useState<TelegramBindingInfo[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [b, p] = await Promise.all([getTelegramBindings(), listProjects()]);
      setBindings(b);
      setProjects(p);
    } catch {
      // Panel is optional — silent.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const key = (b: TelegramBindingInfo) => `${b.chat_id}-${b.thread_id}`;

  const rebind = async (b: TelegramBindingInfo, projectId: string | null) => {
    setBusy(key(b));
    try {
      await patchTelegramBinding({ chat_id: b.chat_id, thread_id: b.thread_id, project_id: projectId });
      await load();
    } catch (e) {
      toast.error("Rebind failed", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(null);
    }
  };

  const unbind = async (b: TelegramBindingInfo) => {
    setBusy(key(b));
    try {
      await deleteTelegramBinding(b.chat_id, b.thread_id);
      await load();
    } catch (e) {
      toast.error("Unbind failed", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(null);
    }
  };

  if (loading) return null;
  if (bindings.length === 0) {
    return (
      <p className="tg-bindings-empty">
        No Telegram chats linked yet — message the bot anywhere to start chatting; /project attaches a topic to a project.
      </p>
    );
  }

  return (
    <div className="tg-bindings">
      <table className="tg-bindings-table">
        <thead>
          <tr>
            <th>Chat / topic</th>
            <th>Project</th>
            <th>Active chat</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {bindings.map((b) => {
            const k = key(b);
            return (
              <tr key={k} className={busy === k ? "tg-bindings-row--busy" : undefined}>
                <td title={`chat ${b.chat_id}${b.thread_id ? ` · topic ${b.thread_id}` : ""}`}>
                  <span className="tg-bindings-kind">{KIND_ICON[b.kind] ?? "💬"}</span>
                  {b.kind === "topic"
                    ? `Topic ${b.thread_id}`
                    : b.kind === "group"
                      ? `Group ${b.chat_id}`
                      : `DM ${b.chat_id}`}
                </td>
                <td>
                  <select
                    className="tg-bindings-select"
                    value={b.project_id ?? ""}
                    disabled={busy === k}
                    onChange={(e) => void rebind(b, e.target.value || null)}
                  >
                    <option value="">— none —</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="tg-bindings-session" title={b.active_session_id ?? ""}>
                  {b.active_session_title || b.active_session_id?.slice(0, 8) || "—"}
                </td>
                <td>
                  <button
                    type="button"
                    className="settings-btn"
                    disabled={busy === k}
                    onClick={() => void unbind(b)}
                    title="Remove the binding — this chat/topic returns to default routing"
                  >
                    Unbind
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
