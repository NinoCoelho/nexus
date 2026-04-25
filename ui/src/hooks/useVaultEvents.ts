/**
 * useVaultEvents — subscribe a component to the vault SSE event stream.
 *
 * The handler is wrapped in a ref so callers can pass an inline arrow
 * function without re-subscribing on every render.
 */
import { useEffect, useRef } from "react";
import { subscribeVaultEvents, type VaultEvent } from "../api";

export function useVaultEvents(handler: (event: VaultEvent) => void): void {
  const ref = useRef(handler);
  ref.current = handler;

  useEffect(() => {
    const unsubscribe = subscribeVaultEvents((e) => ref.current(e));
    return unsubscribe;
  }, []);
}
