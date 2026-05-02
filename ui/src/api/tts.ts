/**
 * @file TTS client — wraps `POST /tts/synthesize` and `GET /tts/voices`.
 *
 * The backend returns:
 *   - 204 (no content) when the configured engine is `webspeech` or `off`
 *     — the caller should fall back to `window.speechSynthesis`.
 *   - 200 with audio bytes (audio/wav or audio/mpeg) for backend engines.
 */
import { BASE } from "./base";

export interface TTSVoice {
  id: string;
  name: string;
  language: string;
}

export interface SynthOptions {
  voice?: string;
  speed?: number;
  signal?: AbortSignal;
  /** When true, the backend summarizes the text first if it's over the
   * cap; otherwise reads verbatim. Click-to-listen sets this; ack
   * pipeline doesn't (acks are already short). */
  summarizeIfLong?: boolean;
  /** Soft word cap — the backend defaults to 500. Override only for
   * advanced use; the user can't currently set this from the UI. */
  capWords?: number;
}

export interface SynthResult {
  blob: Blob;
  mime: string;
  /** True when the backend ran a summarize pass before synthesis. The
   * UI uses this to show a "Reading summary…" toast so the user knows
   * the audio isn't a verbatim read of the full text. */
  summarized: boolean;
}

/** Returns null when the backend returned 204 — caller must use Web Speech. */
export async function synthesize(
  text: string,
  opts: SynthOptions = {},
): Promise<SynthResult | null> {
  const res = await fetch(`${BASE}/tts/synthesize`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      voice: opts.voice,
      speed: opts.speed,
      summarize_if_long: opts.summarizeIfLong ?? false,
      ...(opts.capWords ? { cap_words: opts.capWords } : {}),
    }),
    signal: opts.signal,
  });
  if (res.status === 204) return null;
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `TTS failed: ${res.status}`);
  }
  const mime = res.headers.get("content-type") || "audio/wav";
  const summarized = res.headers.get("X-TTS-Summarized") === "1";
  return { blob: await res.blob(), mime, summarized };
}

export async function listVoices(
  engine?: string,
  language?: string,
): Promise<TTSVoice[]> {
  const params = new URLSearchParams();
  if (engine) params.set("engine", engine);
  if (language) params.set("language", language);
  const url = `${BASE}/tts/voices${params.size ? `?${params}` : ""}`;
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data?.voices) ? data.voices : [];
}

export interface EngineStatus {
  engine: string;
  ready: boolean;
  needs_install: boolean;
  missing_packages: string[];
}

/** Probe whether the engine's Python deps are installed in the running daemon. */
export async function getEngineStatus(engine: string): Promise<EngineStatus> {
  const res = await fetch(
    `${BASE}/tts/status?engine=${encodeURIComponent(engine)}`,
    { credentials: "include" },
  );
  if (!res.ok) {
    return { engine, ready: false, needs_install: false, missing_packages: [] };
  }
  return res.json();
}

/** Trigger pip-install of the engine's missing requirements. Resolves once
 * the install finishes (or rejects with the error tail). */
export async function installEngine(engine: string): Promise<void> {
  const res = await fetch(`${BASE}/tts/install`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `install failed: ${res.status}`);
  }
}
