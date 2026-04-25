// API client for audio transcription.
import { BASE } from "./base";

export async function transcribeAudio(blob: Blob, language?: string): Promise<{ text: string }> {
  const form = new FormData();
  const name = blob.type.includes("webm") ? "audio.webm" : "audio.bin";
  form.append("file", blob, name);
  if (language) form.append("language", language);
  const res = await fetch(`${BASE}/transcribe`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch { /* ignore */ }
    throw new Error(`Transcription failed: ${detail}`);
  }
  return res.json();
}
