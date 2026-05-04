/**
 * @file useVoiceAckPlayer — receives `voice_ack` SessionEvents and plays them.
 *
 * Three gates, ALL required before this client speaks:
 *   1. `document.visibilityState === 'visible'` — tab is foregrounded
 *   2. `view === 'chat'` — user is on the chat view, not vault/calendar/etc.
 *   3. `sessionId === activeSessionId` — user is on THIS specific chat
 *
 * When any gate fails:
 *   - For `complete` acks targeting a *different* session, raise the
 *     existing clickable toast ("Jump to session") so the user has a
 *     durable trail.
 *   - Otherwise stay silent.
 *
 * This stops cross-device chatter (Mac Chrome speaking when user is on
 * the iPhone) and cross-session overlap (two finished tasks talking at
 * the same time).
 *
 * Audio source: prefer the backend-rendered Piper bytes (audio_b64). If
 * synth failed and the bytes are missing, fall back to the OS via
 * window.speechSynthesis with the same transcript — better than silent.
 */
import { useCallback, useEffect, useRef } from "react";
import type { VoiceAckPayload } from "../api/chat";
import { useToast } from "../toast/ToastProvider";

interface UseVoiceAckPlayerArgs {
  /** Currently-viewed session id, so we can suppress noisy starts/progress
   * for background turns and route cross-session completions to the toast. */
  activeSessionId: string | null;
  /** Top-level view name (e.g. "chat", "vault", "calendar"). Audio only
   * fires when the user is on the "chat" view. */
  view: string;
  /** Caller wants control of "jump to this session" UX. */
  onJumpToSession?: (sessionId: string) => void;
}

const _b64ToBlob = (b64: string, mime: string): Blob => {
  const bytes = atob(b64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
  return new Blob([arr], { type: mime || "audio/wav" });
};

const _speakViaWebSpeech = (
  text: string,
  language: string,
  speed: number,
): void => {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = language || "en-US";
  utter.rate = speed || 1.0;
  window.speechSynthesis.speak(utter);
};

export function useVoiceAckPlayer({
  activeSessionId,
  view,
  onJumpToSession,
}: UseVoiceAckPlayerArgs) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const toast = useToast();

  // Live visibility tracked via ref so the gate is read at handle-time
  // without forcing re-renders.
  const visibleRef = useRef(
    typeof document !== "undefined"
      ? document.visibilityState === "visible"
      : true,
  );
  useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => {
      visibleRef.current = document.visibilityState === "visible";
    };
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  const playPayload = useCallback(
    (payload: VoiceAckPayload): void => {
      const text = payload.transcript?.trim();
      if (!text) return;
      // Stop whatever was playing — newer acks always win, an outdated
      // "still working..." right before "all done" is just confusing.
      // `pause()` + `src = ""` alone isn't enough on Chrome / Safari:
      // the underlying media buffer can keep emitting sound for a few
      // hundred ms. `removeAttribute('src') + load()` forces a full
      // reset so the previous audio truly stops before the new one
      // starts (otherwise back-to-back acks "embolam" / overlap).
      if (audioRef.current) {
        const prev = audioRef.current;
        audioRef.current = null;
        try {
          prev.pause();
          prev.removeAttribute("src");
          prev.load();
        } catch {
          // Best-effort cleanup; never let a stale audio crash the new ack.
        }
      }
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
      // Backend synth failed (Piper missing / wrong sample rate / disk
      // error). Fall back to the OS rather than going silent — the user
      // dictated, they expect to hear something.
      if (!payload.audio_b64) {
        _speakViaWebSpeech(text, payload.language, payload.speed);
        return;
      }
      const blob = _b64ToBlob(payload.audio_b64, payload.audio_mime);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        // The decoded blob couldn't be played (codec mismatch, corrupt
        // bytes). Fall through to OS synth so the user still gets the
        // message.
        _speakViaWebSpeech(text, payload.language, payload.speed);
      };
      audioRef.current = audio;
      void audio.play().catch((err) => {
        // Browsers block autoplay until user interaction. Try Web Speech,
        // which sometimes gets a separate exemption.
        // eslint-disable-next-line no-console
        console.warn("[voice_ack] audio.play() blocked, trying Web Speech:", err);
        _speakViaWebSpeech(text, payload.language, payload.speed);
      });
    },
    [],
  );

  const handle = useCallback(
    (sessionId: string, payload: VoiceAckPayload): void => {
      const sameSession = sessionId === activeSessionId;
      const onChatView = view === "chat";
      const visible = visibleRef.current;

      // `notify` events ALWAYS surface as a toast — they're agent-
      // initiated status pings meant to be the attention point even
      // when the user is on another tab or session.
      if (payload.kind === "notify") {
        toast.info(payload.transcript, {
          silent: true,
          duration: 6000,
          action: !sameSession && onJumpToSession
            ? {
                label: "Jump to session",
                onClick: () => onJumpToSession(sessionId),
              }
            : undefined,
        });
        // Notify audio: deliberately permissive. The agent is actively
        // talking TO the user with a status update; suppressing audio
        // because their tab happens to be backgrounded would defeat
        // the purpose. Play whenever it's the same session — even on
        // other tabs/views, even when hidden.
        if (sameSession && payload.audio_b64) {
          // eslint-disable-next-line no-console
          console.info("[voice_ack/notify] playing",
            { hasAudio: true, transcript: payload.transcript.slice(0, 80) });
          playPayload(payload);
        } else if (!payload.audio_b64) {
          // eslint-disable-next-line no-console
          console.warn("[voice_ack/notify] no audio bytes — toast only",
            { sessionId, activeSessionId, transcript: payload.transcript.slice(0, 80) });
        }
        return;
      }

      // Non-notify (start/progress/complete) keeps the strict gate so
      // a long-running task in a backgrounded session doesn't blast
      // audio at someone who's reading something else.
      const canPlay = visible && onChatView && sameSession;
      if (!canPlay) {
        // eslint-disable-next-line no-console
        console.info(
          "[voice_ack] suppressed",
          { kind: payload.kind, sessionId, activeSessionId, view, visible,
            transcript: payload.transcript.slice(0, 80) },
        );
        // Cross-session completion still surfaces a clickable trail —
        // the user might be in a different session but wants to know
        // their long-running task finished.
        if (payload.kind === "complete" && !sameSession) {
          toast.info(payload.transcript, {
            duration: 8000,
            action: onJumpToSession
              ? {
                  label: "Jump to session",
                  onClick: () => onJumpToSession(sessionId),
                }
              : undefined,
          });
        }
        return;
      }
      // eslint-disable-next-line no-console
      console.info("[voice_ack] playing",
        { kind: payload.kind, hasAudio: !!payload.audio_b64,
          transcript: payload.transcript.slice(0, 80) });
      playPayload(payload);
    },
    [activeSessionId, view, onJumpToSession, playPayload, toast],
  );

  // Stop on unmount.
  useEffect(
    () => () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
    },
    [],
  );

  return { handle };
}
