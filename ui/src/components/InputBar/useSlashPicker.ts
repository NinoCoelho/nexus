import { useCallback, useEffect, useRef, useState } from "react";
import { getSlashCommands, type SlashCommand } from "../../api";

/**
 * Drives the slash-command picker visibility + filtering.
 *
 * Trigger: a `/` at character 0 of the input. We deliberately don't detect
 * mid-line `/` because forward slashes are common in URLs, dates, and paths
 * — false positives would be far more annoying than the convenience.
 *
 * The command registry is fetched once on mount and cached for the lifetime
 * of the component; it's small (≪ 20 entries) and static across the
 * process lifetime of the server.
 */

export interface SlashState {
  /** The text after the leading `/`, used to filter the registry. */
  query: string;
}

// Module-level cache so the picker doesn't refetch on every component mount
// (each ChatView re-render after a session switch would otherwise hit the
// endpoint). The registry is process-static server-side.
let _commandsCache: SlashCommand[] | null = null;
let _commandsPromise: Promise<SlashCommand[]> | null = null;

function loadCommands(): Promise<SlashCommand[]> {
  if (_commandsCache) return Promise.resolve(_commandsCache);
  if (_commandsPromise) return _commandsPromise;
  _commandsPromise = getSlashCommands()
    .then((cmds) => {
      _commandsCache = cmds;
      return cmds;
    })
    .catch((err) => {
      _commandsPromise = null; // allow retry on next trigger
      throw err;
    });
  return _commandsPromise;
}

export function useSlashPicker(value: string) {
  const [slash, setSlash] = useState<SlashState | null>(null);
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const loadedRef = useRef(false);

  /** Detect a leading `/<word>` token — only at column 0 of the input. */
  const detectSlash = useCallback((text: string): SlashState | null => {
    if (!text.startsWith("/")) return null;
    // Take everything up to the first whitespace as the query.
    const firstSpace = text.search(/\s/);
    const tail = firstSpace === -1 ? text.slice(1) : text.slice(1, firstSpace);
    // If the user has already typed a space, they're past the command name —
    // hide the picker so it doesn't get in the way of args.
    if (firstSpace !== -1) return null;
    return { query: tail };
  }, []);

  // Lazy-load the registry the first time the picker would appear.
  useEffect(() => {
    if (!slash || loadedRef.current) return;
    loadedRef.current = true;
    loadCommands()
      .then(setCommands)
      .catch(() => {
        loadedRef.current = false; // retry next time
      });
  }, [slash]);

  // Keep `slash` in sync with the input value.
  useEffect(() => {
    setSlash(detectSlash(value));
  }, [value, detectSlash]);

  /** Filtered registry for the current query. Prefix match, case-insensitive. */
  const filtered = slash
    ? commands.filter((c) => c.name.startsWith(slash.query.toLowerCase()))
    : [];

  return { slash, setSlash, commands: filtered };
}
