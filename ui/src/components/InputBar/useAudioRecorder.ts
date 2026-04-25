/**
 * @file Hook for audio recording via the MediaRecorder API.
 *
 * Manages the full recording lifecycle: requesting microphone permission,
 * accumulating audio chunks, and producing an `audio/webm` `Blob` with an
 * object URL when done. Permission errors are reported via toast rather than thrown.
 */
import { useCallback, useRef, useState } from "react";
import { useToast } from "../../toast/ToastProvider";

export interface AudioAttachment {
  blob: Blob;
  url: string;
}

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
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const url = URL.createObjectURL(blob);
        setAudio({ blob, url });
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      toast.error("Microphone access denied");
    }
  }, [toast]);

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }, []);

  const clearAudio = useCallback(() => {
    if (audio) {
      URL.revokeObjectURL(audio.url);
      setAudio(null);
    }
  }, [audio]);

  return { recording, audio, setAudio, startRecording, stopRecording, clearAudio };
}
