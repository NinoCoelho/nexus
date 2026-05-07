/**
 * @file Hook for audio recording via the MediaRecorder API.
 *
 * Manages the full recording lifecycle: requesting microphone permission,
 * accumulating audio chunks, and producing an `audio/webm` `Blob` with an
 * object URL when done. Permission errors are reported via toast rather than thrown.
 *
 * Also exposes a live RMS level history and an elapsed-seconds counter so
 * the UI can render a waveform + timer while recording.
 *
 * VAD (voice activity detection): After a per-recording fingerprint
 * calibration window (first ~1s), silence below the calibrated threshold
 * for 3 consecutive seconds triggers `onSilenceTimeout`. In follow-up
 * mode, if no speech is detected within 10s of recording start, the
 * recording is auto-cancelled and a soft waiting beep plays every 2s
 * to remind the user the mic is listening.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { sounds } from "../../hooks/useSounds";
import { useToast } from "../../toast/ToastProvider";

export interface AudioAttachment {
  blob: Blob;
  url: string;
}

export interface RecorderOptions {
  onSilenceTimeout?: () => void;
  followUpMode?: boolean;
  onFollowUpTimeout?: () => void;
}

const LEVEL_HISTORY = 32;
const FINGERPRINT_DURATION_MS = 1000;
const SILENCE_TIMEOUT_MS = 3000;
const FOLLOW_UP_TIMEOUT_MS = 10000;
const WAITING_BEEP_INTERVAL_MS = 2000;
const DEFAULT_THRESHOLD = 0.05;
const MIN_THRESHOLD = 0.02;

export function useAudioRecorder() {
  const toast = useToast();
  const [recording, setRecording] = useState(false);
  const [audio, setAudio] = useState<AudioAttachment | null>(null);
  const [levels, setLevels] = useState<number[]>(() => new Array(LEVEL_HISTORY).fill(0));
  const [seconds, setSeconds] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const cancelledRef = useRef(false);
  const onCompleteRef = useRef<((a: AudioAttachment) => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);

  const vadOptsRef = useRef<RecorderOptions>({});
  const fingerprintSamplesRef = useRef<number[]>([]);
  const fingerprintDoneRef = useRef(false);
  const thresholdRef = useRef(DEFAULT_THRESHOLD);
  const speechDetectedRef = useRef(false);
  const silenceStartRef = useRef<number | null>(null);
  const silenceTimerRef = useRef<number | null>(null);
  const followUpTimerRef = useRef<number | null>(null);
  const waitingBeepRef = useRef<number | null>(null);
  const vadStoppedRef = useRef(false);
  const recordingStartRef = useRef<number>(0);

  const clearVadTimers = useCallback(() => {
    if (silenceTimerRef.current != null) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (followUpTimerRef.current != null) {
      clearTimeout(followUpTimerRef.current);
      followUpTimerRef.current = null;
    }
    if (waitingBeepRef.current != null) {
      clearInterval(waitingBeepRef.current);
      waitingBeepRef.current = null;
    }
    silenceStartRef.current = null;
  }, []);

  const resetVad = useCallback(() => {
    clearVadTimers();
    fingerprintSamplesRef.current = [];
    fingerprintDoneRef.current = false;
    thresholdRef.current = DEFAULT_THRESHOLD;
    speechDetectedRef.current = false;
    vadStoppedRef.current = false;
    recordingStartRef.current = 0;
  }, [clearVadTimers]);

  const cleanupAnalyser = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (timerRef.current != null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    clearVadTimers();
    try { sourceRef.current?.disconnect(); } catch { /* ignore */ }
    try { analyserRef.current?.disconnect(); } catch { /* ignore */ }
    try { void audioCtxRef.current?.close(); } catch { /* ignore */ }
    sourceRef.current = null;
    analyserRef.current = null;
    audioCtxRef.current = null;
  }, [clearVadTimers]);

  useEffect(() => () => cleanupAnalyser(), [cleanupAnalyser]);

  const startRecording = useCallback(async (opts?: RecorderOptions) => {
    vadOptsRef.current = opts ?? {};
    resetVad();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const candidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4;codecs=mp4a.40.2",
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

      const startedAt = Date.now();
      recordingStartRef.current = startedAt;

      if (opts?.followUpMode) {
        waitingBeepRef.current = window.setInterval(() => {
          if (!speechDetectedRef.current && !vadStoppedRef.current) {
            sounds.micWaiting();
          }
        }, WAITING_BEEP_INTERVAL_MS);

        followUpTimerRef.current = window.setTimeout(() => {
          if (!speechDetectedRef.current && !vadStoppedRef.current) {
            vadStoppedRef.current = true;
            if (waitingBeepRef.current != null) {
              clearInterval(waitingBeepRef.current);
              waitingBeepRef.current = null;
            }
            cancelledRef.current = true;
            onCompleteRef.current = null;
            mediaRecorderRef.current?.stop();
            setRecording(false);
            opts.onFollowUpTimeout?.();
          }
        }, FOLLOW_UP_TIMEOUT_MS);
      }

      const tick = () => {
        if (vadStoppedRef.current) return;
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        const normalized = Math.min(1, rms * 2.4);

        setLevels((prev) => {
          const next = prev.slice(1);
          next.push(normalized);
          return next;
        });

        const elapsed = Date.now() - startedAt;

        if (!fingerprintDoneRef.current && elapsed < FINGERPRINT_DURATION_MS) {
          fingerprintSamplesRef.current.push(rms);
        } else if (!fingerprintDoneRef.current) {
          fingerprintDoneRef.current = true;
          const samples = fingerprintSamplesRef.current;
          if (samples.length > 10) {
            const sorted = [...samples].sort((a, b) => a - b);
            const p75 = sorted[Math.floor(sorted.length * 0.75)];
            thresholdRef.current = Math.max(p75 * 2.5, MIN_THRESHOLD);
          }
        }

        if (fingerprintDoneRef.current) {
          if (rms > thresholdRef.current) {
            if (!speechDetectedRef.current) {
              speechDetectedRef.current = true;
              if (followUpTimerRef.current != null) {
                clearTimeout(followUpTimerRef.current);
                followUpTimerRef.current = null;
              }
              if (waitingBeepRef.current != null) {
                clearInterval(waitingBeepRef.current);
                waitingBeepRef.current = null;
              }
            }
            silenceStartRef.current = null;
            if (silenceTimerRef.current != null) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
          } else if (speechDetectedRef.current) {
            if (silenceStartRef.current == null) {
              silenceStartRef.current = Date.now();
            }
            if (silenceTimerRef.current == null) {
              silenceTimerRef.current = window.setTimeout(() => {
                if (vadStoppedRef.current) return;
                const since = silenceStartRef.current;
                if (since != null && (Date.now() - since) >= SILENCE_TIMEOUT_MS - 200) {
                  silenceTimerRef.current = null;
                  vadStoppedRef.current = true;
                  vadOptsRef.current.onSilenceTimeout?.();
                }
              }, SILENCE_TIMEOUT_MS);
            }
          }
        }

        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);

      timerRef.current = window.setInterval(() => {
        setSeconds(Math.floor((Date.now() - startedAt) / 1000));
      }, 250);

      setRecording(true);
    } catch {
      toast.error("Microphone access denied");
    }
  }, [cleanupAnalyser, resetVad, toast]);

  const stopRecording = useCallback(
    (opts?: { onComplete?: (audio: AudioAttachment) => void }) => {
      vadStoppedRef.current = true;
      clearVadTimers();
      cancelledRef.current = false;
      onCompleteRef.current = opts?.onComplete ?? null;
      mediaRecorderRef.current?.stop();
      setRecording(false);
    },
    [clearVadTimers],
  );

  const cancelRecording = useCallback(() => {
    vadStoppedRef.current = true;
    clearVadTimers();
    cancelledRef.current = true;
    onCompleteRef.current = null;
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }, [clearVadTimers]);

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
