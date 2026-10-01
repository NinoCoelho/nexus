/**
 * Context preamble for the coordinator bubble.
 *
 * Messages sent from the floating master-chat bubble carry a `<context>`
 * block telling the coordinator where the user currently is (view, project,
 * app, vault file, chat). The block is prepended to the message text —
 * the same client-side pattern the Chrome side panel uses for its page
 * pointer. It is transport metadata, not content: every render surface
 * strips it before showing the user bubble.
 */

export interface BubbleLocation {
  /** Active route view (chat / projects / apps / vault / calendar / kanban / workflows / advanced). */
  view: string;
  /** Selected project id in the Projects view, when any. */
  projectId?: string | null;
  /** Selected app folder in the Apps view. */
  appFolder?: string | null;
  /** Selected vault file path. */
  vaultPath?: string | null;
  /** Selected kanban board path. */
  kanbanPath?: string | null;
  /** Selected calendar path. */
  calendarPath?: string | null;
  /** Active chat session id (only meaningful when view === "chat"). */
  activeSessionId?: string | null;
  /** Project id of the active chat session, when it is a project chat. */
  activeProjectId?: string | null;
}

export interface BubbleContextInfo {
  /** Short human label for the chip in the panel header (e.g. "Projects · Apollo"). */
  label: string;
  /** Preamble body lines (without the wrapping tags). */
  lines: string[];
}

/** Matches a leading `<context>…</context>` block (tolerant to leading blanks). */
const CONTEXT_BLOCK_RE = /^[ \t]*<context>[\s\S]*?<\/context>[ \t]*(?:\n+|$)/;

/** Remove a leading `<context>` block from a message for display. */
export function stripContextPreamble(text: string): string {
  if (!text || !text.includes("<context>")) return text;
  return text.replace(CONTEXT_BLOCK_RE, "");
}

/**
 * Turn the current App location into a compact preamble descriptor.
 * Returns null when nothing interesting can be said (empty app shell).
 */
export function describeBubbleLocation(loc: BubbleLocation, projectName?: (id: string) => string | undefined): BubbleContextInfo | null {
  const lines: string[] = [];
  let label = loc.view;

  const projName = (id?: string | null) => (id ? projectName?.(id) || id : undefined);

  switch (loc.view) {
    case "chat": {
      if (loc.activeProjectId) {
        const n = projName(loc.activeProjectId);
        lines.push(`view: chat (inside project "${n ?? loc.activeProjectId}")`);
        lines.push(`project_id: ${loc.activeProjectId}`);
        label = `Chat · ${n ?? "project"}`;
      } else {
        lines.push("view: chat (unprojected)");
        label = "Chat";
      }
      if (loc.activeSessionId && loc.activeSessionId !== "__new__") {
        lines.push(`active_chat_session: ${loc.activeSessionId}`);
      }
      break;
    }
    case "projects": {
      if (loc.projectId) {
        const n = projName(loc.projectId);
        lines.push(`view: projects, selected project "${n ?? loc.projectId}"`);
        lines.push(`project_id: ${loc.projectId}`);
        label = `Projects · ${n ?? "project"}`;
      } else {
        lines.push("view: projects (project list)");
        label = "Projects";
      }
      break;
    }
    case "apps": {
      if (loc.appFolder) {
        lines.push(`view: apps, selected app "${loc.appFolder}"`);
        label = `Apps · ${loc.appFolder.split("/").pop() ?? loc.appFolder}`;
      } else {
        lines.push("view: apps (app list)");
        label = "Apps";
      }
      break;
    }
    case "vault": {
      if (loc.vaultPath) {
        lines.push(`view: vault, open file ${loc.vaultPath}`);
        label = `Vault · ${(loc.vaultPath.split("/").pop() ?? loc.vaultPath).replace(/\.md$/, "")}`;
      } else {
        lines.push("view: vault (file tree)");
        label = "Vault";
      }
      break;
    }
    case "kanban": {
      if (loc.kanbanPath) {
        lines.push(`view: kanban board ${loc.kanbanPath}`);
        label = `Kanban · ${(loc.kanbanPath.split("/").pop() ?? loc.kanbanPath).replace(/\.md$/, "")}`;
      } else {
        lines.push("view: kanban (board list)");
        label = "Kanban";
      }
      break;
    }
    case "calendar": {
      if (loc.calendarPath) {
        lines.push(`view: calendar ${loc.calendarPath}`);
        label = "Calendar";
      } else {
        lines.push("view: calendar");
        label = "Calendar";
      }
      break;
    }
    case "workflows": {
      if (loc.vaultPath) {
        lines.push(`view: workflows, open workflow ${loc.vaultPath}`);
        label = `Workflows · ${(loc.vaultPath.split("/").pop() ?? loc.vaultPath).replace(/\.md$/, "")}`;
      } else {
        lines.push("view: workflows");
        label = "Workflows";
      }
      break;
    }
    default: {
      lines.push(`view: ${loc.view}`);
      break;
    }
  }

  if (lines.length === 0) return null;
  return { label, lines };
}

/** Build the full preamble block prepended to a bubble message. */
export function buildContextPreamble(info: BubbleContextInfo): string {
  return `<context>\n${info.lines.join("\n")}\n</context>\n\n`;
}
