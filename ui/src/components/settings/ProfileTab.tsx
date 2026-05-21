import { useState } from "react";
import { useToast } from "../../toast/ToastProvider";
import { setPassword } from "../../api/auth";
import { useSession } from "../SessionProvider";
import SettingsField from "./SettingsField";
import SettingsSection from "./SettingsSection";

export default function ProfileTab() {
  const toast = useToast();
  const { user, refresh } = useSession();
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);

  const handleChangePassword = async () => {
    if (newPw.length < 6) {
      toast.error("Password must be at least 6 characters");
      return;
    }
    if (newPw !== confirmPw) {
      toast.error("Passwords do not match");
      return;
    }
    setBusy(true);
    try {
      await setPassword(newPw);
      toast.success("Password updated");
      setNewPw("");
      setConfirmPw("");
      refresh();
    } catch (err) {
      toast.error("Failed to update password", {
        detail: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <SettingsSection title="Account" icon="&#x1F464;">
        <SettingsField label="Email">
          <input
            type="email"
            value={user?.email ?? ""}
            readOnly
            className="settings-input"
            style={{ opacity: 0.6 }}
          />
        </SettingsField>
        <SettingsField label="Display name">
          <input
            type="text"
            value={user?.display_name ?? ""}
            readOnly
            className="settings-input"
            style={{ opacity: 0.6 }}
          />
        </SettingsField>
        <SettingsField label="Role">
          <input
            type="text"
            value={user?.role ?? ""}
            readOnly
            className="settings-input"
            style={{ opacity: 0.6, textTransform: "capitalize" }}
          />
        </SettingsField>
      </SettingsSection>

      <SettingsSection title="Change Password" icon="&#x1F512;">
        {!user?.has_password && (
          <div style={{ fontSize: 13, opacity: 0.7, marginBottom: 8 }}>
            You haven{"\u2019"}t set a password yet. Set one to log in from other browsers.
          </div>
        )}
        <SettingsField label="New password">
          <input
            type="password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            placeholder="Min 6 characters"
            minLength={6}
            className="settings-input"
            autoComplete="new-password"
          />
        </SettingsField>
        <SettingsField label="Confirm password">
          <input
            type="password"
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            placeholder="Re-enter password"
            className="settings-input"
            autoComplete="new-password"
          />
        </SettingsField>
        <button
          type="button"
          className="settings-btn"
          disabled={busy || newPw.length < 6 || newPw !== confirmPw}
          onClick={handleChangePassword}
        >
          {busy ? "Saving..." : user?.has_password ? "Update Password" : "Set Password"}
        </button>
      </SettingsSection>
    </>
  );
}
