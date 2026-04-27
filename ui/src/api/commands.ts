/**
 * Slash-command registry — server-side single source of truth.
 * Fetched once and cached for the picker that pops up when the user types `/`
 * at the start of the chat input.
 */
import { BASE } from "./base";

export interface SlashCommand {
  name: string;
  description: string;
  args_hint: string;
}

export async function getSlashCommands(): Promise<SlashCommand[]> {
  const res = await fetch(`${BASE}/commands`);
  if (!res.ok) throw new Error(`commands error: ${res.status}`);
  return res.json();
}
