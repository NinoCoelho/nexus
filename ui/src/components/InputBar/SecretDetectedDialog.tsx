/**
 * SecretDetectedDialog — pre-send modal for the chat input bar.
 *
 * Triggered by the regex/entropy guard in useSecretGuard. Three actions:
 *
 *   - Save as credential → name it, store in ~/.nexus/secrets.toml,
 *     and replace the matched substring in the textarea with `$NAME`.
 *     Skill bodies and tool args carrying the placeholder are
 *     substituted at the tool boundary.
 *   - Send anyway → send the message untouched, with a header that tells
 *     the server to skip its backstop redaction.
 *   - Cancel → returns focus to the textarea.
 */

import { useState } from "react";
import { createPortal } from "react-dom";
import { setCredential } from "../../api";
import { useToast } from "../../toast/ToastProvider";
import "../Modal.css";

interface Props {
  detected: string;
  onSaveAsCredential: (name: string) => void;
  onSendAnyway: () => void;
  onCancel: () => void;
}

const NAME_RE = /^[A-Z][A-Z0-9_]*$/;

function maskPreview(value: string): string {
  if (value.length <= 12) return "••••";
  return `${value.slice(0, 4)}…${value.slice(-4)}`;
}

export default function SecretDetectedDialog({
  detected,
  onSaveAsCredential,
  onSendAnyway,
  onCancel,
}: Props) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  async function handleSave() {
    if (!NAME_RE.test(name)) {
      toast.error("Name must be UPPER_SNAKE_CASE", {
        detail: "Pattern: ^[A-Z][A-Z0-9_]*$",
      });
      return;
    }
    setSaving(true);
    try {
      await setCredential(name, detected, { kind: "generic" });
      toast.success(`Saved as $${name}`);
      onSaveAsCredential(name);
    } catch (e) {
      toast.error("Failed to save credential", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  }

  // Portal to document.body so the modal escapes the InputBar's
  // ancestor `.bottom-region`, which has `backdrop-filter` set —
  // backdrop-filter creates a containing block that traps `position:
  // fixed` descendants and would render the modal inside the input bar.
  return createPortal(
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">This looks like an API key</div>
        <div className="modal-message">
          The text you're about to send contains{" "}
          <code>{maskPreview(detected)}</code>, which matches a known
          credential pattern. Sending it would expose the key to the LLM
          (and to its provider's logging policy). You can save it locally
          and use <code>${name || "NAME"}</code> as a placeholder instead —
          the value is substituted at the tool boundary, never sent to the
          LLM.
        </div>
        <input
          className="modal-input"
          type="text"
          placeholder="GITHUB_TOKEN"
          value={name}
          autoCapitalize="characters"
          spellCheck={false}
          autoFocus
          onChange={(e) => setName(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (NAME_RE.test(name)) void handleSave();
            }
            if (e.key === "Escape") onCancel();
          }}
        />
        <div className="modal-actions">
          <button className="modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="modal-btn" onClick={onSendAnyway}>
            Send anyway
          </button>
          <button
            className="modal-btn modal-btn--primary"
            disabled={!NAME_RE.test(name) || saving}
            onClick={() => void handleSave()}
          >
            {saving ? "Saving…" : "Save as credential"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
