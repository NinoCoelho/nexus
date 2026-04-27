import { useEffect, useRef, useState } from "react";
import { BrandMark } from "./BrandMark";
import "./SplashScreen.css";

const SESSION_KEY = "nexus.splashShown.v1";

function shouldShow(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (sessionStorage.getItem(SESSION_KEY) === "1") return false;
  } catch {
    /* sessionStorage unavailable — show anyway */
  }
  return true;
}

function playChime(): void {
  try {
    const Ctor =
      (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    // Soft major arpeggio: C5 → E5 → G5
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
    // A subtle low pad to give it body
    const pad = ctx.createOscillator();
    const padGain = ctx.createGain();
    pad.type = "sine";
    pad.frequency.value = 130.81; // C3
    padGain.gain.setValueAtTime(0, t0);
    padGain.gain.linearRampToValueAtTime(0.025, t0 + 0.2);
    padGain.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.4);
    pad.connect(padGain).connect(ctx.destination);
    pad.start(t0);
    pad.stop(t0 + 1.5);
    setTimeout(() => ctx.close().catch(() => {}), 1800);
  } catch {
    /* autoplay blocked or no audio support — silent fallback */
  }
}

export function SplashScreen() {
  const [show, setShow] = useState<boolean>(shouldShow);
  const [leaving, setLeaving] = useState(false);
  const dismissedRef = useRef(false);

  useEffect(() => {
    if (!show) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const total = reduce ? 900 : 2200;
    const fadeOut = reduce ? 240 : 520;

    playChime();

    const dismiss = () => {
      if (dismissedRef.current) return;
      dismissedRef.current = true;
      setLeaving(true);
      try {
        sessionStorage.setItem(SESSION_KEY, "1");
      } catch {
        /* ignore */
      }
      window.setTimeout(() => setShow(false), fadeOut);
    };

    const auto = window.setTimeout(dismiss, total);
    const onKey = () => dismiss();
    const onPointer = () => dismiss();
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    return () => {
      window.clearTimeout(auto);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [show]);

  if (!show) return null;
  return (
    <div
      className={`nexus-splash ${leaving ? "is-leaving" : "is-entering"}`}
      role="dialog"
      aria-label="Nexus is loading"
      aria-live="polite"
    >
      <div className="nexus-splash__bg" aria-hidden="true" />
      <div className="nexus-splash__ring nexus-splash__ring--a" aria-hidden="true" />
      <div className="nexus-splash__ring nexus-splash__ring--b" aria-hidden="true" />
      <div className="nexus-splash__banner">
        <BrandMark size="lg" />
      </div>
      <div className="nexus-splash__hint">tap or press any key to skip</div>
    </div>
  );
}
