import { useState, useEffect, useCallback, useRef } from "react";
import { listCredentials, type Credential } from "../api/credentials";

let cache: Credential[] | null = null;
let pending: Promise<Credential[]> | null = null;

async function fetchCredentials(): Promise<Credential[]> {
  if (cache) return cache;
  if (!pending) {
    pending = listCredentials()
      .then((list) => {
        cache = list;
        pending = null;
        return list;
      })
      .catch((e) => {
        pending = null;
        throw e;
      });
  }
  return pending;
}

function invalidateCache() {
  cache = null;
  pending = null;
}

export function useCredentials() {
  const [credentials, setCredentials] = useState<Credential[]>(cache ?? []);
  const [loading, setLoading] = useState(!cache);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    if (!cache) {
      fetchCredentials()
        .then((list) => {
          if (mounted.current) setCredentials(list);
        })
        .catch(() => {})
        .finally(() => {
          if (mounted.current) setLoading(false);
        });
    }
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async (): Promise<Credential[]> => {
    invalidateCache();
    setLoading(true);
    try {
      const list = await fetchCredentials();
      if (mounted.current) {
        setCredentials(list);
        setLoading(false);
      }
      return list;
    } catch {
      if (mounted.current) setLoading(false);
      return [];
    }
  }, []);

  return { credentials, loading, reload };
}
