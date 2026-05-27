import { useSession } from "../SessionProvider";
import SettingsField from "./SettingsField";
import SettingsSection from "./SettingsSection";

export default function ProfileTab() {
  const { user } = useSession();

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
    </>
  );
}
