import type { AudioAttachment } from "./useAudioRecorder";

interface AttachedFile {
  name: string;
  vaultPath: string;
}

interface Props {
  attachments?: AttachedFile[];
  audio: AudioAttachment | null;
  transcribing: boolean;
  onRemoveAttachment: (idx: number) => void;
  onClearAudio: () => void;
}

export default function AttachmentsBar({ attachments, audio, transcribing, onRemoveAttachment, onClearAudio }: Props) {
  if (!((attachments && attachments.length > 0) || audio)) return null;
  return (
    <div className="input-attachments">
      {attachments?.map((a, i) => (
        <span key={i} className="input-attachment-chip">
          <svg width="11" height="11" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
            <polyline points="12 2 12 6 16 6" />
          </svg>
          {a.name}
          <button className="input-attachment-remove" onClick={() => onRemoveAttachment(i)} type="button">&times;</button>
        </span>
      ))}
      {audio && (
        <span className="input-attachment-chip input-attachment-chip--audio">
          <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 10 Q6 6 10 10 Q14 14 18 10" strokeDasharray="2 2" />
          </svg>
          {transcribing ? "Transcribing…" : "Voice memo"}
          <button
            className="input-attachment-remove"
            onClick={onClearAudio}
            type="button"
            disabled={transcribing}
          >&times;</button>
        </span>
      )}
    </div>
  );
}
