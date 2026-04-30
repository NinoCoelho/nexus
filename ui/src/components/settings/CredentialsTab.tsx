/**
 * CredentialsTab — manage entries in ~/.nexus/secrets.toml.
 *
 * The agent uses these for `$NAME` substitution at tool boundaries
 * (env vars take precedence — see secrets.resolve in the backend).
 * Skills that declare `requires_keys` add entries here on first use.
 */

import { useEffect, useState } from "react";
import {
  deleteCredential,
  listCredentials,
  setCredential,
  type Credential,
} from "../../api";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import SettingsField from "./SettingsField";
import SettingsSection from "./SettingsSection";

const NAME_RE = /^[A-Z][A-Z0-9_]*$/;

export default function CredentialsTab() {
  const toast = useToast();
  const [items, setItems] = useState<Credential[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newSkill, setNewSkill] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      setItems(await listCredentials());
    } catch (e) {
      toast.error("Failed to load credentials", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!NAME_RE.test(newName)) {
      toast.error("Name must be UPPER_SNAKE_CASE", {
        detail: "Pattern: ^[A-Z][A-Z0-9_]*$ (matches the env-var convention)",
      });
      return;
    }
    if (!newValue) {
      toast.error("Value cannot be empty");
      return;
    }
    try {
      await setCredential(newName, newValue, {
        kind: newSkill ? "skill" : "generic",
        skill: newSkill || undefined,
      });
      toast.success(`Saved ${newName}`);
      setNewName("");
      setNewValue("");
      setNewSkill("");
      await refresh();
    } catch (e) {
      toast.error("Failed to save", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleEditSubmit(name: string, value: string) {
    setEditing(null);
    try {
      const existing = items?.find((i) => i.name === name);
      await setCredential(name, value, {
        kind: existing?.kind ?? "generic",
        skill: existing?.skill ?? undefined,
      });
      toast.success(`Updated ${name}`);
      await refresh();
    } catch (e) {
      toast.error("Failed to update", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleDeleteConfirmed(name: string) {
    setConfirmDelete(null);
    try {
      await deleteCredential(name);
      toast.success(`Deleted ${name}`);
      await refresh();
    } catch (e) {
      toast.error("Failed to delete", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  return (
    <>
      <SettingsSection
        title="Credentials"
        icon="🔑"
        description={
          <>
            API keys and tokens stored at <code>~/.nexus/secrets.toml</code>{" "}
            (file mode 0600). Use them in skills as{" "}
            <code>$NAME</code> placeholders — the server substitutes the value
            at the tool boundary, the LLM never sees the raw key. Environment
            variables of the same name take precedence and are not listed here.
          </>
        }
      >
        <form onSubmit={handleAdd} className="creds-add-form">
          <SettingsField label="Name" hint="UPPER_SNAKE_CASE, e.g. GITHUB_TOKEN" layout="row">
            <input
              type="text"
              className="settings-input"
              value={newName}
              placeholder="GITHUB_TOKEN"
              autoCapitalize="characters"
              spellCheck={false}
              onChange={(e) => setNewName(e.target.value.toUpperCase())}
            />
          </SettingsField>
          <SettingsField label="Value" hint="Stored locally; never sent to the LLM." layout="row">
            <input
              type="password"
              className="settings-input"
              value={newValue}
              autoComplete="new-password"
              spellCheck={false}
              onChange={(e) => setNewValue(e.target.value)}
            />
          </SettingsField>
          <SettingsField
            label="Skill (optional)"
            hint="Tag the credential as belonging to a specific skill, for your own reference."
            layout="row"
          >
            <input
              type="text"
              className="settings-input"
              value={newSkill}
              placeholder="github_issues"
              onChange={(e) => setNewSkill(e.target.value)}
            />
          </SettingsField>
          <div className="creds-add-actions">
            <button type="submit" className="settings-btn settings-btn--primary">
              Save credential
            </button>
          </div>
        </form>
      </SettingsSection>

      <SettingsSection title="Stored credentials" icon="📋">
        {loading && !items && <p className="s-field__hint">Loading…</p>}
        {items && items.length === 0 && (
          <p className="s-field__hint">No credentials stored yet.</p>
        )}
        {items && items.length > 0 && (
          <table className="creds-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Skill</th>
                <th>Value</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.name}>
                  <td>
                    <code>{c.name}</code>
                  </td>
                  <td>{c.kind}</td>
                  <td>{c.skill ?? "—"}</td>
                  <td className="creds-table__masked">{c.masked}</td>
                  <td className="creds-table__actions">
                    <button
                      type="button"
                      className="settings-btn"
                      onClick={() => setEditing(c.name)}
                    >
                      Update
                    </button>
                    <button
                      type="button"
                      className="settings-btn settings-btn--danger"
                      onClick={() => setConfirmDelete(c.name)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SettingsSection>

      {editing && (
        <Modal
          kind="prompt"
          secret
          title={`Update ${editing}`}
          message="Enter the new value. The current value is replaced; nothing else changes."
          placeholder="new value"
          confirmLabel="Save"
          onCancel={() => setEditing(null)}
          onSubmit={(v) => void handleEditSubmit(editing, v)}
        />
      )}

      {confirmDelete && (
        <Modal
          kind="confirm"
          danger
          title={`Delete ${confirmDelete}?`}
          message="This removes the value from ~/.nexus/secrets.toml. Skills that depend on it will be re-prompted on next use."
          confirmLabel="Delete"
          onCancel={() => setConfirmDelete(null)}
          onSubmit={() => void handleDeleteConfirmed(confirmDelete)}
        />
      )}
    </>
  );
}
