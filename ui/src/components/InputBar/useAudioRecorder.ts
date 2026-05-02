/**
 * @file Hook for audio recording via the MediaRecorder API.
 *
 * Manages the full recording lifecycle: requesting microphone permission,
 * accumulating audio chunks, and producing an `audio/webm` `Blob` with an
 * object URL when done. Permission errors are reported via toast rather than thrown.
 *
 * Also exposes a live RMS level history and an elapsed-seconds counter so
 * the UI can render a waveform + timer while recording.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "../../toast/ToastProvider";

export interface AudioAttachment {
  blob: Blob;
  url: string;
}

const LEVEL_HISTORY = 32; // ring-buffer length for the live waveform

/**
 * Audio recording hook for the input bar.
 *
 * @returns
 *   - `recording` — `true` while recording is active.
 *   - `audio` — result of the last recording (`blob` + object `url`); `null` when cleared.
 *   - `setAudio` — direct setter for the audio state (used by the component when consuming the blob).
 *   - `startRecording` — request microphone access and start recording.
 *   - `stopRecording` — stop recording and populate `audio`.
 *   - `clearAudio` — revoke the object URL and clear the state; call when discarding audio.
 *
 * @example
 * ```tsx
 * const { recording, audio, startRecording, stopRecording, clearAudio } = useAudioRecorder();
 * ```
 */
export function useAudioRecorder() {
  const toast = useToast();
  const [recording, setRecording] = useState(false);
  const [audio, setAudio] = useState<AudioAttachment | null>(null);
  const [levels, setLevels] = useState<number[]>(() => new Array(LEVEL_HISTORY).fill(0));
  const [seconds, setSeconds] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const cancelledRef = useRef(false);
  // When set, the next stop delivers the blob to this callback instead of
  // populating `audio` state — used by the press-and-hold / tap-tap flow that
  // wants to transcribe and send without ever showing an attachment chip.
  const onCompleteRef = useRef<((a: AudioAttachment) => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);

  const cleanupAnalyser = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (timerRef.current != null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    try { sourceRef.current?.disconnect(); } catch { /* ignore */ }
    try { analyserRef.current?.disconnect(); } catch { /* ignore */ }
    try { void audioCtxRef.current?.close(); } catch { /* ignore */ }
    sourceRef.current = null;
    analyserRef.current = null;
    audioCtxRef.current = null;
  }, []);

  useEffect(() => () => cleanupAnalyser(), [cleanupAnalyser]);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // iOS Safari and desktop browsers disagree on default codec. Pick the
      // best supported format up-front so we know what we're producing —
      // hardcoding `audio/webm` was breaking iPhone recordings (Safari
      // produces audio/mp4 + AAC by default and the resulting blob would
      // get mislabelled, breaking faster-whisper transcription).
      const candidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4;codecs=mp4a.40.2",   // iOS AAC
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ];
      const isSupported = (typeof MediaRecorder !== "undefined" && typeof (MediaRecorder as any).isTypeSupported === "function")
        ? (m: string) => (MediaRecorder as any).isTypeSupported(m)
        : (_m: string) => false;
      const chosenMime = candidates.find(isSupported) || "";
      const recorder = chosenMime
        ? new MediaRecorder(stream, { mimeType: chosenMime })
        : new MediaRecorder(stream);
      chunksRef.current = [];
      cancelledRef.current = false;
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        cleanupAnalyser();
        if (!cancelledRef.current) {
          // Use the recorder's actual mime — it might differ from what
          // we asked for if the browser substituted. Strip the ;codecs=
          // suffix because backends sniffing by extension don't care.
          const actualMime = recorder.mimeType || chosenMime || "audio/webm";
          const blob = new Blob(chunksRef.current, { type: actualMime });
          const url = URL.createObjectURL(blob);
          const cb = onCompleteRef.current;
          onCompleteRef.current = null;
          if (cb) cb({ blob, url });
          else setAudio({ blob, url });
        } else {
          onCompleteRef.current = null;
        }
        stream.getTracks().forEach((t) => t.stop());
        setLevels(new Array(LEVEL_HISTORY).fill(0));
        setSeconds(0);
      };
      mediaRecorderRef.current = recorder;
      recorder.start();

      // Wire up an analyser for the live waveform.
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      audioCtxRef.current = ctx;
      sourceRef.current = src;
      analyserRef.current = analyser;
      const buf = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        // Compute normalised RMS (~0..1).
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        setLevels((prev) => {
          const next = prev.slice(1);
          next.push(Math.min(1, rms * 2.4));
          return next;
        });
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);

      const startedAt = Date.now();
      timerRef.current = window.setInterval(() => {
        setSeconds(Math.floor((Date.now() - startedAt) / 1000));
      }, 250);

      setRecording(true);
    } catch {
      toast.error("Microphone access denied");
    }
  }, [cleanupAnalyser, toast]);

  const stopRecording = useCallback(
    (opts?: { onComplete?: (audio: AudioAttachment) => void }) => {
      cancelledRef.current = false;
      onCompleteRef.current = opts?.onComplete ?? null;
      mediaRecorderRef.current?.stop();
      setRecording(false);
    },
    [],
  );

  const cancelRecording = useCallback(() => {
    cancelledRef.current = true;
    onCompleteRef.current = null;
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }, []);

  const clearAudio = useCallback(() => {
    if (audio) {
      URL.revokeObjectURL(audio.url);
      setAudio(null);
    }
  }, [audio]);

  return {
    recording, audio, setAudio,
    levels, seconds,
    startRecording, stopRecording, cancelRecording, clearAudio,
  };
}
