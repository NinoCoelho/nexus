let unlocked = false;

export function unlockAudioForIOS(): void {
  if (unlocked) return;
  unlocked = true;
  if (typeof window === "undefined") return;
  try {
    const Ctx =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    if (Ctx) {
      const ctx = new Ctx();
      if (typeof ctx.resume === "function") void ctx.resume().catch(() => {});
      const buffer = ctx.createBuffer(1, 1, 22050);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      if (typeof source.start === "function") source.start(0);
    }
    const SILENT_WAV =
      "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
    const a = new Audio(SILENT_WAV);
    a.muted = false;
    a.volume = 0;
    void a.play().then(() => a.pause()).catch(() => {});
  } catch {
    // Not fatal — Web Speech fallback in the player still works.
  }
}
