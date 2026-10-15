/**
 * Shared bot directory — one fetch per tab, shared across every consumer
 * (mention links and the forward picker in AssistantMessage, the composer's
 * @-autocomplete, team chips). Bots change rarely; the cache never expires
 * within a tab (creating/editing a bot then mentioning it is rare enough
 * that a reload fixes it).
 */
import { useEffect, useState } from "react";
import { getBots, type Bot } from "../api";

export interface BotRef {
  id: string;
  handle: string;
  name: string;
  kind: Bot["kind"];
}

let cache: BotRef[] | null = null;
let inflight: Promise<BotRef[]> | null = null;

export function fetchBotDirectory(): Promise<BotRef[]> {
  if (cache) return Promise.resolve(cache);
  if (!inflight) {
    inflight = getBots()
      .then((bots) => {
        cache = bots.map((b) => ({ id: b.id, handle: b.handle || b.id, name: b.name, kind: b.kind }));
        return cache;
      })
      .catch(() => {
        inflight = null;
        return [] as BotRef[];
      });
  }
  return inflight;
}

export function useBotDirectory(): BotRef[] {
  const [bots, setBots] = useState<BotRef[]>(cache ?? []);
  useEffect(() => {
    let alive = true;
    void fetchBotDirectory().then((list) => {
      if (alive) setBots(list);
    });
    return () => { alive = false; };
  }, []);
  return bots;
}
