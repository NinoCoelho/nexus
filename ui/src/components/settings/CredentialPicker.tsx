/**
 * CredentialPicker — shared UI for binding something (a provider, a skill
 * config, …) to a named entry in the credential store.
 *
 * Renders a select with three groups of options:
 *   1. ``(none)`` — clears the binding.
 *   2. Every credential currently stored.
 *   3. ``+ Create new credential…`` — opens a modal that takes a name and
 *      a masked value, saves to the store, and selects the new entry.
 *
 * The picker never reads or shows a credential's raw value; only its name.
 */

import { useEffect, useState } from "react";
import {
  listCredentials,
  setCredential,
  type Credential,
} from "../../api";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import "../SettingsDrawer.css";

interface Props {
  /** Currently bound credential name, or null when unset. */
  value: string | null;
  /** Called with the new credential name (or null to clear). */
  onChange: (name: string | null) => void;
  /** Pre-fills the name field of the "create new" modal. */
  defaultNameSuggestion?: string;
  /** Optional filter to narrow which stored credentials appear in the list. */
  filter?: (c: Credential) => boolean;
  /** Optional placeholder shown when nothing is selected. */
  placeholder?: string;
  disabled?: boolean;
}

const NAME_RE = /^[A-Z][A-Z0-9_]*$/;
const CREATE_NEW_SENTINEL = "__nx_create_new__";
const NONE_SENTINEL = "";

export default function CredentialPicker({
  value,
  onChange,
  defaultNameSuggestion = "",
  filter,
  placeholder = "(none — fall back to env var)",
  disabled = false,
}: Props) {
  const toast = useToast();
  const [creds, setCreds] = useState<Credential[] | null>(null);
  const [creating, setCreating] = useState(false);
  // Form state for the "Create new" modal lives here so we can keep both
  // fields (name + value) rather than fight Modal's single-input prompt
  // shape. We use a custom modal-shaped div, not Modal itself.
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);
  // For the inline confirm-clear dialog when the user picks (none) on a
  // currently-bound provider.
  const [confirmClear, setConfirmClear] = useState(false);

  async function refresh() {
    try {
      setCreds(await listCredentials());
    } catch (e) {
      toast.error("Failed to load credentials", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visible = (creds ?? []).filter((c) => (filter ? filter(c) : true));

  function handleSelect(next: string) {
    if (next === CREATE_NEW_SENTINEL) {
      setNewName(defaultNameSuggestion);
      setNewValue("");
      setCreating(true);
      return;
    }
    if (next === NONE_SENTINEL) {
      // If currently bound, confirm before clearing — clearing means the
      // provider falls back to legacy/env paths and may stop working.
      if (value) setConfirmClear(true);
      else onChange(null);
      return;
    }
    onChange(next);
  }

  async function handleCreateSubmit() {
    if (!NAME_RE.test(newName)) {
      toast.error("Name must be UPPER_SNAKE_CASE", {
        detail: "Pattern: ^[A-Z][A-Z0-9_]*$",
      });
      return;
    }
    if (!newValue) {
      toast.error("Value cannot be empty");
      return;
    }
    setSaving(true);
    try {
      await setCredential(newName, newValue, { kind: "generic" });
      toast.success(`Saved $${newName}`);
      await refresh();
      onChange(newName);
      setCreating(false);
    } catch (e) {
      toast.error("Failed to save credential", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <select
        className="settings-input"
        value={value ?? NONE_SENTINEL}
        disabled={disabled}
        onChange={(e) => handleSelect(e.target.value)}
      >
        <option value={NONE_SENTINEL}>{placeholder}</option>
        {visible.length > 0 && (
          <optgroup label="Stored credentials">
            {visible.map((c) => (
              <option key={c.name} value={c.name}>
                ${c.name} — {c.masked}
                {c.kind !== "generic" ? ` (${c.kind})` : ""}
              </option>
            ))}
          </optgroup>
        )}
        <option value={CREATE_NEW_SENTINEL}>+ Create new credential…</option>
      </select>

      {creating && (
        <CreateModal
          newName={newName}
          newValue={newValue}
          saving={saving}
          onNameChange={setNewName}
          onValueChange={setNewValue}
          onCancel={() => setCreating(false)}
          onSubmit={() => void handleCreateSubmit()}
        />
      )}

      {confirmClear && (
        <Modal
          kind="confirm"
          danger
          title="Clear credential link?"
          message={`The provider will fall back to its legacy env-var or inline-key path. The credential ${value} stays in the store.`}
          confirmLabel="Clear"
          onCancel={() => setConfirmClear(false)}
          onSubmit={() => {
            setConfirmClear(false);
            onChange(null);
          }}
        />
      )}
    </>
  );
}

interface CreateModalProps {
  newName: string;
  newValue: string;
  saving: boolean;
  onNameChange: (v: string) => void;
  onValueChange: (v: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

function CreateModal({
  newName,
  newValue,
  saving,
  onNameChange,
  onValueChange,
  onCancel,
  onSubmit,
}: CreateModalProps) {
  const canSave = NAME_RE.test(newName) && newValue.length > 0 && !saving;
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">New credential</div>
        <div className="modal-message">
          Stored locally at <code>~/.nexus/secrets.toml</code> (file mode 0600).
          The LLM never sees the raw value — it's substituted at the tool
          boundary when referenced as <code>$NAME</code>.
        </div>

        <label className="modal-field-label">Name (UPPER_SNAKE_CASE)</label>
        <input
          className="modal-input"
          type="text"
          autoFocus
          autoCapitalize="characters"
          spellCheck={false}
          value={newName}
          placeholder="OPENAI_API_KEY"
          onChange={(e) => onNameChange(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === "Escape") onCancel();
          }}
        />

        <label className="modal-field-label">Value</label>
        <input
          className="modal-input"
          type="password"
          autoComplete="new-password"
          spellCheck={false}
          value={newValue}
          onChange={(e) => onValueChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && canSave) {
              e.preventDefault();
              onSubmit();
            }
            if (e.key === "Escape") onCancel();
          }}
        />

        <div className="modal-actions">
          <button className="modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn--primary"
            disabled={!canSave}
            onClick={onSubmit}
          >
            {saving ? "Saving…" : "Save credential"}
          </button>
        </div>
      </div>
    </div>
  );
}
