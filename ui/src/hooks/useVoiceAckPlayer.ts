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
  activeSessionId: string | null;
  view: string;
  onJumpToSession?: (sessionId: string) => void;
  /** Fired when a voice ack finishes playing (audio onended or Web Speech
   *  onend). Receives the ack kind so callers can distinguish complete
   *  (turn done, safe to re-open mic) from start/progress. */
  onPlaybackDone?: (kind: VoiceAckPayload["kind"]) => void;
}

const _b64ToBlob = (b64: string, mime: string): Blob => {
  const bytes = atob(b64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
  return new Blob([arr], { type: mime || "audio/wav" });
};

export function useVoiceAckPlayer({
  activeSessionId,
  view,
  onJumpToSession,
  onPlaybackDone,
}: UseVoiceAckPlayerArgs) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const lastKindRef = useRef<VoiceAckPayload["kind"] | null>(null);
  const webSpeechUtteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const toast = useToast();

  const onPlaybackDoneRef = useRef(onPlaybackDone);
  onPlaybackDoneRef.current = onPlaybackDone;

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

  const _firePlaybackDone = useCallback(() => {
    const kind = lastKindRef.current;
    if (kind) {
      lastKindRef.current = null;
      onPlaybackDoneRef.current?.(kind);
      try {
        window.dispatchEvent(new CustomEvent("nexus:voice-ack-done", {
          detail: { kind },
        }));
      } catch { /* SSR / test env */ }
    }
  }, []);

  const _speakViaWebSpeech = useCallback(
    (text: string, language: string, speed: number): void => {
      if (typeof window === "undefined" || !window.speechSynthesis) return;
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = language || "en-US";
      utter.rate = speed || 1.0;
      utter.onend = () => {
        webSpeechUtteranceRef.current = null;
        _firePlaybackDone();
      };
      utter.onerror = () => {
        webSpeechUtteranceRef.current = null;
        _firePlaybackDone();
      };
      webSpeechUtteranceRef.current = utter;
      window.speechSynthesis.speak(utter);
    },
    [_firePlaybackDone],
  );

  const playPayload = useCallback(
    (payload: VoiceAckPayload): void => {
      const text = payload.transcript?.trim();
      if (!text) return;
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
        webSpeechUtteranceRef.current = null;
      }
      lastKindRef.current = payload.kind;

      if (!payload.audio_b64) {
        _speakViaWebSpeech(text, payload.language, payload.speed);
        return;
      }
      const blob = _b64ToBlob(payload.audio_b64, payload.audio_mime);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        audioRef.current = null;
        _firePlaybackDone();
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        audioRef.current = null;
        _speakViaWebSpeech(text, payload.language, payload.speed);
      };
      audioRef.current = audio;
      void audio.play().catch((err) => {
        // eslint-disable-next-line no-console
        console.warn("[voice_ack] audio.play() blocked, trying Web Speech:", err);
        audioRef.current = null;
        _speakViaWebSpeech(text, payload.language, payload.speed);
      });
    },
    [_speakViaWebSpeech, _firePlaybackDone],
  );

  const handle = useCallback(
    (sessionId: string, payload: VoiceAckPayload): void => {
      const sameSession = sessionId === activeSessionId;
      const onChatView = view === "chat";
      const visible = visibleRef.current;

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

      const canPlay = visible && onChatView && sameSession;
      if (!canPlay) {
        // eslint-disable-next-line no-console
        console.info(
          "[voice_ack] suppressed",
          { kind: payload.kind, sessionId, activeSessionId, view, visible,
            transcript: payload.transcript.slice(0, 80) },
        );
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

  useEffect(
    () => () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
      webSpeechUtteranceRef.current = null;
    },
    [],
  );

  return { handle };
}
