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
import SettingsSection from "./SettingsSection";

const NAME_RE = /^[A-Z][A-Z0-9_]*$/;

export default function CredentialsTab() {
  const { t } = useTranslation("settings");
  const toast = useToast();
  const [items, setItems] = useState<Credential[] | null>(null);
  const [loading, setLoading] = useState(false);

  const [addOpen, setAddOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");

  const [selected, setSelected] = useState<Credential | null>(null);
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
  }, []);

  function openAdd() {
    setNewName("");
    setNewValue("");
    setAddOpen(true);
  }

  async function handleAdd() {
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
      await setCredential(newName, newValue, { kind: "generic" });
      toast.success(t("settings:credentials.toast.saved", { name: newName }));
      setAddOpen(false);
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
        <button
          type="button"
          className="settings-btn settings-btn--primary creds-add-btn"
          onClick={openAdd}
        >
          {t("settings:credentials.addButton")}
        </button>

        {loading && !items && (
          <p className="s-field__hint">{t("settings:credentials.loading")}</p>
        )}
        {items && items.length === 0 && (
          <p className="s-field__hint">{t("settings:credentials.empty")}</p>
        )}
        {items && items.length > 0 && (
          <table className="creds-table">
            <thead>
              <tr>
                <th>{t("settings:credentials.tableColName")}</th>
                <th>{t("settings:credentials.tableColValue")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr
                  key={c.name}
                  className="creds-table__row--clickable"
                  onClick={() => setSelected(c)}
                >
                  <td>
                    <code>{c.name}</code>
                  </td>
                  <td className="creds-table__masked">{c.masked}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SettingsSection>

      {addOpen && (
        <div className="modal-backdrop" onClick={() => setAddOpen(false)}>
          <div
            className="modal-dialog"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAddOpen(false);
              if (e.key === "Enter") { e.preventDefault(); void handleAdd(); }
            }}
          >
            <div className="modal-title">
              {t("settings:credentials.addModalTitle")}
            </div>
            <div className="creds-modal-fields">
              <div className="s-field">
                <label className="s-field__label">
                  {t("settings:credentials.nameLabel")}
                </label>
                <p className="s-field__hint">
                  {t("settings:credentials.nameHint")}
                </p>
                <input
                  type="text"
                  className="settings-input"
                  value={newName}
                  placeholder={t("settings:credentials.namePlaceholder")}
                  autoCapitalize="characters"
                  spellCheck={false}
                  autoFocus
                  onChange={(e) => setNewName(e.target.value.toUpperCase())}
                />
              </div>
              <div className="s-field">
                <label className="s-field__label">
                  {t("settings:credentials.valueLabel")}
                </label>
                <p className="s-field__hint">
                  {t("settings:credentials.valueHint")}
                </p>
                <input
                  type="password"
                  className="settings-input"
                  value={newValue}
                  autoComplete="new-password"
                  spellCheck={false}
                  onChange={(e) => setNewValue(e.target.value)}
                />
              </div>
            </div>
            <div className="modal-actions">
              <button className="modal-btn" onClick={() => setAddOpen(false)}>
                {t("common:buttons.cancel")}
              </button>
              <button
                className="modal-btn modal-btn--primary"
                onClick={() => void handleAdd()}
                disabled={!NAME_RE.test(newName) || !newValue}
              >
                {t("settings:credentials.addModalSave")}
              </button>
            </div>
          </div>
        </div>
      )}

      {selected && !editing && !confirmDelete && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div
            className="modal-dialog"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") setSelected(null);
            }}
          >
            <div className="modal-title">
              {t("settings:credentials.detailModalTitle", { name: selected.name })}
            </div>
            <div className="creds-detail-body">
              <div className="creds-detail-row">
                <span className="creds-detail-label">
                  {t("settings:credentials.detailValueLabel")}
                </span>
                <span className="creds-detail-value">{selected.masked}</span>
              </div>
            </div>
            <div className="modal-actions">
              <button
                className="modal-btn"
                onClick={() => {
                  const name = selected.name;
                  setSelected(null);
                  setEditing(name);
                }}
              >
                {t("settings:credentials.updateButton")}
              </button>
              <button
                className="modal-btn modal-btn--danger"
                onClick={() => {
                  const name = selected.name;
                  setSelected(null);
                  setConfirmDelete(name);
                }}
              >
                {t("settings:credentials.deleteButton")}
              </button>
            </div>
          </div>
        </div>
      )}

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
