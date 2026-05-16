/**
 * Sound effects for chat / HITL / agent events. Tones are synthesized
 * with the Web Audio API so there are no asset files to ship. A
 * single AudioContext is reused; per-effect volume and a global mute
 * flag are persisted in localStorage and shared across tabs via the
 * storage event.
 */
import { useCallback, useEffect, useState } from "react";

export type SoundKey =
  | "finalResponse"
  | "notification"
  | "popupOpen"
  | "countdownTick"
  | "attention"
  | "agentStep"
  | "micReady"
  | "micWaiting"
  | "micSilence";

/** Display labels for the settings UI. */
export const SOUND_LABELS: Record<SoundKey, string> = {
  finalResponse: "Final response",
  notification: "Notification (toasts)",
  popupOpen: "Approval popup",
  countdownTick: "Countdown",
  attention: "Reminder (every minute)",
  agentStep: "Agent step",
  micReady: "Mic ready (follow-up)",
  micWaiting: "Mic waiting beep",
  micSilence: "Silence detected (auto-send)",
};

/** Per-effect multiplier (0..1). Default 1.0 = baseline volume. */
const DEFAULT_VOLUMES: Record<SoundKey, number> = {
  finalResponse: 1,
  notification: 1,
  popupOpen: 1,
  countdownTick: 1,
  attention: 1,
  agentStep: 1,
  micReady: 1,
  micWaiting: 1,
  micSilence: 1,
};

const MUTE_KEY = "nx.soundMuted";
const VOL_KEY = "nx.soundVolumes";
const MUTE_EVENT = "nx-sound-muted-changed";
const VOL_EVENT = "nx-sound-volumes-changed";

// ── Mute ─────────────────────────────────────────────────────────────────────

function readMuted(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeMuted(v: boolean) {
  try {
    window.localStorage.setItem(MUTE_KEY, v ? "1" : "0");
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event(MUTE_EVENT));
}

// ── Per-effect volumes ───────────────────────────────────────────────────────

function readVolumes(): Record<SoundKey, number> {
  if (typeof window === "undefined") return { ...DEFAULT_VOLUMES };
  try {
    const raw = window.localStorage.getItem(VOL_KEY);
    if (!raw) return { ...DEFAULT_VOLUMES };
    const parsed = JSON.parse(raw) as Partial<Record<SoundKey, number>>;
    const out = { ...DEFAULT_VOLUMES };
    for (const k of Object.keys(DEFAULT_VOLUMES) as SoundKey[]) {
      const v = parsed[k];
      if (typeof v === "number" && v >= 0 && v <= 1) out[k] = v;
    }
    return out;
  } catch {
    return { ...DEFAULT_VOLUMES };
  }
}

function writeVolumes(v: Record<SoundKey, number>) {
  try {
    window.localStorage.setItem(VOL_KEY, JSON.stringify(v));
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event(VOL_EVENT));
}

// ── AudioContext ─────────────────────────────────────────────────────────────

let _ctx: AudioContext | null = null;
function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!_ctx) {
    type Ctor = typeof AudioContext;
    const w = window as unknown as { AudioContext?: Ctor; webkitAudioContext?: Ctor };
    const Ctx = w.AudioContext ?? w.webkitAudioContext;
    if (!Ctx) return null;
    try {
      _ctx = new Ctx();
    } catch {
      return null;
    }
  }
  if (_ctx.state === "suspended") void _ctx.resume();
  return _ctx;
}

// ── Synthesis ────────────────────────────────────────────────────────────────

interface ToneOpts {
  freq: number;
  duration: number;
  volume?: number;
  type?: OscillatorType;
  attack?: number;
  release?: number;
  delay?: number;
}

function tone(opts: ToneOpts) {
  const ctx = getCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime + (opts.delay ?? 0);
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = opts.type ?? "sine";
  osc.frequency.value = opts.freq;
  const vol = Math.min(1, Math.max(0, opts.volume ?? 0.2));
  const attack = opts.attack ?? 0.008;
  const release = opts.release ?? 0.08;
  gain.gain.setValueAtTime(0, t0);
  gain.gain.linearRampToValueAtTime(vol, t0 + attack);
  gain.gain.setValueAtTime(vol, t0 + attack + opts.duration);
  gain.gain.linearRampToValueAtTime(0, t0 + attack + opts.duration + release);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + attack + opts.duration + release + 0.05);
}

/**
 * Baseline waveforms for each effect. The numeric ``volume`` here is
 * the "100%" mark — the slider in settings scales these down. Tweaking
 * a baseline changes how loud "full volume" is; tweaking the slider
 * just attenuates relative to that.
 *
 * Several effects use two oscillators in sequence to make a small
 * arpeggio — these tones are part of the same effect and share one
 * slider.
 */
const RECIPES: Record<SoundKey, ToneOpts[]> = {
  finalResponse: [
    { freq: 660, duration: 0.10, volume: 0.22 },
    { freq: 880, duration: 0.16, volume: 0.22, delay: 0.10 },
  ],
  notification: [
    { freq: 740, duration: 0.07, volume: 0.18 },
    { freq: 990, duration: 0.10, volume: 0.18, delay: 0.08 },
  ],
  popupOpen: [
    { freq: 540, duration: 0.14, volume: 0.30, type: "triangle" },
    { freq: 720, duration: 0.18, volume: 0.30, type: "triangle", delay: 0.14 },
  ],
  countdownTick: [
    { freq: 1200, duration: 0.04, volume: 0.16 },
  ],
  attention: [
    { freq: 880, duration: 0.08, volume: 0.22 },
    { freq: 880, duration: 0.08, volume: 0.22, delay: 0.18 },
  ],
  agentStep: [
    { freq: 130, duration: 0.07, volume: 0.30, type: "sine", release: 0.08 },
  ],
  micReady: [
    { freq: 520, duration: 0.10, volume: 0.18 },
    { freq: 780, duration: 0.14, volume: 0.18, delay: 0.10 },
  ],
  micWaiting: [
    { freq: 200, duration: 0.05, volume: 0.08 },
    { freq: 260, duration: 0.06, volume: 0.08, delay: 0.06 },
  ],
  micSilence: [
    { freq: 600, duration: 0.08, volume: 0.15 },
    { freq: 400, duration: 0.12, volume: 0.15, delay: 0.08 },
  ],
};

function play(key: SoundKey) {
  if (readMuted()) return;
  const mult = readVolumes()[key];
  if (mult <= 0) return;
  for (const t of RECIPES[key]) {
    tone({ ...t, volume: (t.volume ?? 0.2) * mult });
  }
}

export const sounds: Record<SoundKey, () => void> = {
  finalResponse: () => play("finalResponse"),
  notification: () => play("notification"),
  popupOpen: () => play("popupOpen"),
  countdownTick: () => play("countdownTick"),
  attention: () => play("attention"),
  agentStep: () => play("agentStep"),
  micReady: () => play("micReady"),
  micWaiting: () => play("micWaiting"),
  micSilence: () => play("micSilence"),
};

/** Number of distinct tones in this effect's recipe (1 or 2). */
export function soundToneCount(key: SoundKey): number {
  return RECIPES[key].length;
}

/** Ordered list of all effect keys (for rendering settings UI). */
export const SOUND_KEYS: SoundKey[] = [
  "finalResponse",
  "notification",
  "popupOpen",
  "attention",
  "countdownTick",
  "agentStep",
  "micReady",
  "micWaiting",
  "micSilence",
];

// ── React hooks ──────────────────────────────────────────────────────────────

/** Read & toggle the global mute flag. */
export function useSoundMute() {
  const [muted, setMutedState] = useState<boolean>(readMuted);
  useEffect(() => {
    const onChange = () => setMutedState(readMuted());
    window.addEventListener(MUTE_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(MUTE_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);
  const setMuted = useCallback((v: boolean) => writeMuted(v), []);
  return { muted, setMuted };
}

/** Read & set per-effect volumes (each value is a 0..1 multiplier). */
export function useSoundVolumes() {
  const [volumes, setVolumesState] = useState<Record<SoundKey, number>>(readVolumes);
  useEffect(() => {
    const onChange = () => setVolumesState(readVolumes());
    window.addEventListener(VOL_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(VOL_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);
  const setVolume = useCallback((key: SoundKey, v: number) => {
    const clamped = Math.min(1, Math.max(0, v));
    const next = { ...readVolumes(), [key]: clamped };
    writeVolumes(next);
  }, []);
  return { volumes, setVolume };
}

/** Imperative read (for non-React call sites). */
export function isSoundMuted(): boolean {
  return readMuted();
}
