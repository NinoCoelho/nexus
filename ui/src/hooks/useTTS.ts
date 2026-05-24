/**
 * @file useTTS — click-to-listen playback for arbitrary text.
 *
 * Always routes through the backend `/tts/synthesize`. The bundled Piper
 * engine is the only path; on first daemon start the default voices are
 * pre-downloaded so synthesis is immediate by the time anyone hits a
 * speaker button.
 *
 * Each invocation cancels whatever was playing before — there's no
 * queueing, the user almost always wants the most recent click to win.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getConfig, type TTSConfig } from "../api/config";
import { synthesize } from "../api/tts";
import { markdownToPlaintext } from "../lib/markdownToPlaintext";
import { useToast } from "../toast/ToastProvider";

export type TTSState = "idle" | "loading" | "playing";

export interface UseTTS {
  state: TTSState;
  /** Synthesize and play. Cancels any in-flight or playing utterance first. */
  speak: (text: string, opts?: { stripMarkdown?: boolean }) => Promise<void>;
  stop: () => void;
  /** True when the user has TTS configured to be visible. UI buttons hide
   * when false. */
  available: boolean;
}

// Singleton config cache shared across all useTTS callers so we don't
// fetch /config on every assistant message render. Refreshed via the
// `nexus:tts-config-changed` window event (fired by VoiceSection on save).
let _ttsCfgCache: TTSConfig | null = null;
let _ttsCfgPromise: Promise<TTSConfig | null> | null = null;
async function _loadTTSConfig(): Promise<TTSConfig | null> {
  if (_ttsCfgCache) return _ttsCfgCache;
  if (_ttsCfgPromise) return _ttsCfgPromise;
  _ttsCfgPromise = getConfig()
    .then((cfg) => {
      _ttsCfgCache = cfg.tts ?? null;
      return _ttsCfgCache;
    })
    .catch(() => null)
    .finally(() => {
      _ttsCfgPromise = null;
    });
  return _ttsCfgPromise;
}
export function invalidateTTSConfigCache(): void {
  _ttsCfgCache = null;
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("nexus:tts-config-changed"));
  }
}

export function useTTS(): UseTTS {
  const [tts, setTts] = useState<TTSConfig | null>(null);
  const [state, setState] = useState<TTSState>("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const toast = useToast();

  useEffect(() => {
    let alive = true;
    void _loadTTSConfig().then((c) => alive && setTts(c));
    const onChange = () => {
      void _loadTTSConfig().then((c) => alive && setTts(c));
    };
    window.addEventListener("nexus:tts-config-changed", onChange);
    return () => {
      alive = false;
      window.removeEventListener("nexus:tts-config-changed", onChange);
    };
  }, []);

  const enabled = tts?.enabled ?? true;

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    setState("idle");
  }, []);

  const speak = useCallback(
    async (text: string, opts?: { stripMarkdown?: boolean }) => {
      if (!enabled) return;
      const clean = (opts?.stripMarkdown ?? true) ? markdownToPlaintext(text) : text;
      if (!clean.trim()) return;
      stop();
      setState("loading");

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        // Voice + speed live entirely on the backend now (auto-detect by
        // text language, fixed 1.0 rate). Click-to-listen always asks
        // the backend to summarize-if-long so a 5,000-word vault note
        // doesn't read for 20 minutes — the backend prepends "Here's a
        // summary." and returns the X-TTS-Summarized header.
        const result = await synthesize(clean, {
          signal: controller.signal,
          summarizeIfLong: true,
        });
        if (controller.signal.aborted) return;
        if (!result) {
          // Backend returned 204 (TTS disabled mid-flight). Just stop.
          setState("idle");
          return;
        }
        if (result.summarized) {
          toast.info("Reading summary…", { duration: 4000 });
        }
        const url = URL.createObjectURL(result.blob);
        const audio = new Audio(url);
        audio.onended = () => {
          URL.revokeObjectURL(url);
          setState("idle");
        };
        audio.onerror = () => {
          URL.revokeObjectURL(url);
          setState("idle");
        };
        audioRef.current = audio;
        setState("playing");
        await audio.play();
      } catch (err) {
        if (controller.signal.aborted) return;
        // eslint-disable-next-line no-console
        console.warn("[tts] synthesis failed:", err);
        setState("idle");
      } finally {
        abortRef.current = null;
      }
    },
    [enabled, stop, toast],
  );

  // Stop any playback if the hook unmounts mid-speech.
  useEffect(() => () => stop(), [stop]);

  return { state, speak, stop, available: enabled };
}
