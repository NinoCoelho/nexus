/**
 * CredentialsTab — manage entries in ~/.nexus/secrets.toml.
 *
 * The agent uses these for `$NAME` substitution at tool boundaries
 * (env vars take precedence — see secrets.resolve in the backend).
 * Skills that declare `requires_keys` add entries here on first use.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("settings");
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
      toast.error(t("settings:credentials.toast.loadFailed"), {
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
      toast.error(t("settings:credentials.toast.nameInvalid"), {
        detail: t("settings:credentials.toast.nameInvalidDetail"),
      });
      return;
    }
    if (!newValue) {
      toast.error(t("settings:credentials.toast.valueEmpty"));
      return;
    }
    try {
      await setCredential(newName, newValue, {
        kind: newSkill ? "skill" : "generic",
        skill: newSkill || undefined,
      });
      toast.success(t("settings:credentials.toast.saved", { name: newName }));
      setNewName("");
      setNewValue("");
      setNewSkill("");
      await refresh();
    } catch (e) {
      toast.error(t("settings:credentials.toast.saveFailed"), {
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
      toast.success(t("settings:credentials.toast.updated", { name }));
      await refresh();
    } catch (e) {
      toast.error(t("settings:credentials.toast.updateFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleDeleteConfirmed(name: string) {
    setConfirmDelete(null);
    try {
      await deleteCredential(name);
      toast.success(t("settings:credentials.toast.deleted", { name }));
      await refresh();
    } catch (e) {
      toast.error(t("settings:credentials.toast.deleteFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  return (
    <>
      <SettingsSection
        title={t("settings:credentials.sectionTitle")}
        icon={t("settings:credentials.sectionIcon")}
        description={t("settings:credentials.sectionDescription")}
      >
        <form onSubmit={handleAdd} className="creds-add-form">
          <SettingsField label={t("settings:credentials.nameLabel")} hint={t("settings:credentials.nameHint")} layout="row">
            <input
              type="text"
              className="settings-input"
              value={newName}
              placeholder={t("settings:credentials.namePlaceholder")}
              autoCapitalize="characters"
              spellCheck={false}
              onChange={(e) => setNewName(e.target.value.toUpperCase())}
            />
          </SettingsField>
          <SettingsField label={t("settings:credentials.valueLabel")} hint={t("settings:credentials.valueHint")} layout="row">
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
            label={t("settings:credentials.skillLabel")}
            hint={t("settings:credentials.skillHint")}
            layout="row"
          >
            <input
              type="text"
              className="settings-input"
              value={newSkill}
              placeholder={t("settings:credentials.skillPlaceholder")}
              onChange={(e) => setNewSkill(e.target.value)}
            />
          </SettingsField>
          <div className="creds-add-actions">
            <button type="submit" className="settings-btn settings-btn--primary">
              {t("settings:credentials.saveButton")}
            </button>
          </div>
        </form>
      </SettingsSection>

      <SettingsSection title={t("settings:credentials.storedTitle")} icon={t("settings:credentials.storedIcon")}>
        {loading && !items && <p className="s-field__hint">{t("settings:credentials.loading")}</p>}
        {items && items.length === 0 && (
          <p className="s-field__hint">{t("settings:credentials.empty")}</p>
        )}
        {items && items.length > 0 && (
          <table className="creds-table">
            <thead>
              <tr>
                <th>{t("settings:credentials.tableColName")}</th>
                <th>{t("settings:credentials.tableColKind")}</th>
                <th>{t("settings:credentials.tableColSkill")}</th>
                <th>{t("settings:credentials.tableColValue")}</th>
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
                      {t("settings:credentials.updateButton")}
                    </button>
                    <button
                      type="button"
                      className="settings-btn settings-btn--danger"
                      onClick={() => setConfirmDelete(c.name)}
                    >
                      {t("settings:credentials.deleteButton")}
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
          title={t("settings:credentials.editModalTitle", { name: editing })}
          message={t("settings:credentials.editModalMessage")}
          placeholder={t("settings:credentials.editModalPlaceholder")}
          confirmLabel={t("settings:credentials.editModalSave")}
          onCancel={() => setEditing(null)}
          onSubmit={(v) => void handleEditSubmit(editing, v)}
        />
      )}

      {confirmDelete && (
        <Modal
          kind="confirm"
          danger
          title={t("settings:credentials.deleteModalTitle", { name: confirmDelete })}
          message={t("settings:credentials.deleteModalMessage")}
          confirmLabel={t("settings:credentials.deleteModalCta")}
          onCancel={() => setConfirmDelete(null)}
          onSubmit={() => void handleDeleteConfirmed(confirmDelete)}
        />
      )}
    </>
  );
}
