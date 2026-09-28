import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteSiteCredential,
  listSiteCredentials,
  setSiteCredential,
  type SiteCredential,
} from "../../api";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import SettingsSection from "./SettingsSection";

/**
 * Site logins — per-site username/password pairs stored Fernet-encrypted
 * in ~/.nexus/site_credentials.db and used exclusively for browser login
 * fills (site_credentials tool: CDP debug Chrome / Chrome side panel).
 * The password is only ever written; the UI can't read it back.
 */
export default function SiteLoginsSection() {
  const { t } = useTranslation("settings");
  const toast = useToast();
  const [items, setItems] = useState<SiteCredential[] | null>(null);
  const [loading, setLoading] = useState(false);

  const [addOpen, setAddOpen] = useState(false);
  const [newSite, setNewSite] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      setItems(await listSiteCredentials());
    } catch (e) {
      toast.error(t("settings:siteLogins.toast.loadFailed"), {
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
    setNewSite("");
    setNewUsername("");
    setNewPassword("");
    setAddOpen(true);
  }

  async function handleAdd() {
    if (!newSite.trim() || !newUsername.trim() || !newPassword) {
      toast.error(t("settings:siteLogins.toast.fieldsRequired"));
      return;
    }
    try {
      await setSiteCredential(newSite.trim(), newUsername.trim(), newPassword);
      toast.success(t("settings:siteLogins.toast.saved", { site: newSite.trim() }));
      setAddOpen(false);
      await refresh();
    } catch (e) {
      toast.error(t("settings:siteLogins.toast.saveFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleDeleteConfirmed(site: string) {
    setConfirmDelete(null);
    try {
      await deleteSiteCredential(site);
      toast.success(t("settings:siteLogins.toast.deleted", { site }));
      await refresh();
    } catch (e) {
      toast.error(t("settings:siteLogins.toast.deleteFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  return (
    <>
      <SettingsSection
        title={t("settings:siteLogins.sectionTitle")}
        icon={t("settings:siteLogins.sectionIcon")}
        description={t("settings:siteLogins.sectionDescription")}
      >
        <button
          type="button"
          className="settings-btn settings-btn--primary creds-add-btn"
          onClick={openAdd}
        >
          {t("settings:siteLogins.addButton")}
        </button>

        {loading && !items && (
          <p className="s-field__hint">{t("settings:siteLogins.loading")}</p>
        )}
        {items && items.length === 0 && (
          <p className="s-field__hint">{t("settings:siteLogins.empty")}</p>
        )}
        {items && items.length > 0 && (
          <table className="creds-table">
            <thead>
              <tr>
                <th>{t("settings:siteLogins.tableColSite")}</th>
                <th>{t("settings:siteLogins.tableColUsername")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.site}>
                  <td>
                    <code>{c.site}</code>
                  </td>
                  <td>{c.username}</td>
                  <td>
                    <button
                      type="button"
                      className="settings-btn"
                      onClick={() => setConfirmDelete(c.site)}
                    >
                      {t("settings:siteLogins.deleteButton")}
                    </button>
                  </td>
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
              if (e.key === "Enter") {
                e.preventDefault();
                void handleAdd();
              }
            }}
          >
            <div className="modal-title">
              {t("settings:siteLogins.addModalTitle")}
            </div>
            <div className="creds-modal-fields">
              <div className="s-field">
                <label className="s-field__label">
                  {t("settings:siteLogins.siteLabel")}
                </label>
                <p className="s-field__hint">{t("settings:siteLogins.siteHint")}</p>
                <input
                  type="text"
                  className="settings-input"
                  value={newSite}
                  placeholder="example.com"
                  spellCheck={false}
                  autoFocus
                  onChange={(e) => setNewSite(e.target.value)}
                />
              </div>
              <div className="s-field">
                <label className="s-field__label">
                  {t("settings:siteLogins.usernameLabel")}
                </label>
                <input
                  type="text"
                  className="settings-input"
                  value={newUsername}
                  spellCheck={false}
                  onChange={(e) => setNewUsername(e.target.value)}
                />
              </div>
              <div className="s-field">
                <label className="s-field__label">
                  {t("settings:siteLogins.passwordLabel")}
                </label>
                <p className="s-field__hint">
                  {t("settings:siteLogins.passwordHint")}
                </p>
                <input
                  type="password"
                  className="settings-input"
                  value={newPassword}
                  autoComplete="new-password"
                  spellCheck={false}
                  onChange={(e) => setNewPassword(e.target.value)}
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
                disabled={!newSite.trim() || !newUsername.trim() || !newPassword}
              >
                {t("settings:siteLogins.addModalSave")}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <Modal
          kind="confirm"
          danger
          title={t("settings:siteLogins.deleteModalTitle", { site: confirmDelete })}
          message={t("settings:siteLogins.deleteModalMessage")}
          confirmLabel={t("settings:siteLogins.deleteModalCta")}
          onCancel={() => setConfirmDelete(null)}
          onSubmit={() => void handleDeleteConfirmed(confirmDelete)}
        />
      )}
    </>
  );
}
