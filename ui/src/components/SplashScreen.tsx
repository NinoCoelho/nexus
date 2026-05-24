import { useEffect, useRef, useState } from "react";
import { BrandMark } from "./BrandMark";
import { BASE } from "../api/base";
import "./SplashScreen.css";

const SESSION_KEY = "nexus.splashShown.v1";
const HEALTH_POLL_MS = 2000;
const HEALTH_TIMEOUT_MS = 3000;

type Mode = "loading" | "branding" | "leaving" | "done";

function sessionSplashShown(): boolean {
  try {
    return sessionStorage.getItem(SESSION_KEY) === "1";
  } catch {
    return false;
  }
}

function markSessionSplashShown(): void {
  try {
    sessionStorage.setItem(SESSION_KEY, "1");
  } catch {
    /* ignore */
  }
}

async function isBackendUp(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), HEALTH_TIMEOUT_MS);
    const res = await fetch(`${BASE}/health`, { signal: ctrl.signal });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

function playChime(): void {
  try {
    const Ctor =
      (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const notes = [523.25, 659.25, 783.99];
    const t0 = ctx.currentTime + 0.05;
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = t0 + i * 0.085;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.07, start + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.7);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.75);
    });
    const pad = ctx.createOscillator();
    const padGain = ctx.createGain();
    pad.type = "sine";
    pad.frequency.value = 130.81;
    padGain.gain.setValueAtTime(0, t0);
    padGain.gain.linearRampToValueAtTime(0.025, t0 + 0.2);
    padGain.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.4);
    pad.connect(padGain).connect(ctx.destination);
    pad.start(t0);
    pad.stop(t0 + 1.5);
    setTimeout(() => ctx.close().catch(() => {}), 1800);
  } catch {
    /* autoplay blocked or no audio support */
  }
}

function removeFallback(): void {
  const el = document.getElementById("nexus-fallback");
  if (el) el.remove();
}

export function SplashScreen() {
  const [mode, setMode] = useState<Mode>("loading");
  const dismissedRef = useRef(false);

  useEffect(() => {
    removeFallback();

    let cancelled = false;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const brandingDuration = reduce ? 900 : 2200;
    const fadeOutDuration = reduce ? 240 : 520;

    function dismiss() {
      if (dismissedRef.current) return;
      dismissedRef.current = true;
      setMode("leaving");
      markSessionSplashShown();
      setTimeout(() => {
        if (!cancelled) setMode("done");
      }, fadeOutDuration);
    }

    async function run() {
      const up = await isBackendUp();
      if (cancelled) return;

      if (up && sessionSplashShown()) {
        dismiss();
        return;
      }

      if (!up) {
        while (!cancelled) {
          await new Promise<void>((r) => setTimeout(r, HEALTH_POLL_MS));
          if (cancelled) return;
          if (await isBackendUp()) break;
        }
        if (cancelled) return;

        if (sessionSplashShown()) {
          dismiss();
          return;
        }
      }

      setMode("branding");
      playChime();

      const auto = setTimeout(dismiss, brandingDuration);
      const onKey = () => dismiss();
      const onPointer = () => dismiss();
      window.addEventListener("keydown", onKey);
      window.addEventListener("pointerdown", onPointer);

      return () => {
        clearTimeout(auto);
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("pointerdown", onPointer);
      };
    }

    const cleanup = run();
    return () => {
      cancelled = true;
      cleanup?.then?.((fn) => fn?.());
    };
  }, []);

  if (mode === "done") return null;

  const isLeaving = mode === "leaving";
  const isLoading = mode === "loading";

  return (
    <div
      className={`nexus-splash ${isLeaving ? "is-leaving" : "is-entering"} ${isLoading ? "is-loading" : ""}`}
      role="dialog"
      aria-label={isLoading ? "Nexus is starting" : "Nexus is loading"}
      aria-live="polite"
    >
      <div className="nexus-splash__bg" aria-hidden="true" />
      <div className="nexus-splash__ring nexus-splash__ring--a" aria-hidden="true" />
      <div className="nexus-splash__ring nexus-splash__ring--b" aria-hidden="true" />
      {isLoading && <div className="nexus-splash__orbit" aria-hidden="true" />}
      <div className="nexus-splash__banner">
        <BrandMark size="lg" />
      </div>
      <div className="nexus-splash__hint">
        {isLoading ? "Starting Nexus…" : "tap or press any key to skip"}
      </div>
    </div>
  );
}
