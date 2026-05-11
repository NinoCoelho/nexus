import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { type CookieDomain, listCookies, deleteCookies } from "../../api/cookies";
import { useToast } from "../../toast/ToastProvider";
import Modal from "../Modal";
import SettingsSection from "./SettingsSection";

export default function CookiesSection() {
  const { t } = useTranslation("settings");
  const toast = useToast();
  const [domains, setDomains] = useState<CookieDomain[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await listCookies();
      setDomains(d);
    } catch {
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteCookies(deleteTarget);
      toast.success(`Cookies for ${deleteTarget} removed`);
      setDomains((prev) => prev.filter((d) => d.domain !== deleteTarget));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to delete cookies");
    } finally {
      setDeleteTarget(null);
    }
  };

  const totalCookies = domains.reduce((sum, d) => sum + d.count, 0);

  return (
    <SettingsSection
      title={t("settings:features.cookiesTitle", "Web Scraping Cookies")}
      icon="🍪"
      collapsible
      defaultOpen={false}
      help={{
        title: t("settings:features.cookiesHelpTitle", "Cookie Export Extension"),
        body: (
          <>
            Export authenticated session cookies from Chrome for the web
            scraper. Install the Nexus Cookie Export extension, then click it
            on any site you're logged into. Cookies are stored at{" "}
            <code>~/.nexus/cookies/</code> in Netscape format — the web
            scraper picks them up automatically.
          </>
        ),
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 12, opacity: 0.75, lineHeight: 1.5 }}>
          {loading ? (
            "Loading..."
          ) : domains.length > 0 ? (
            <>
              {domains.length} domain{domains.length !== 1 ? "s" : ""},{" "}
              {totalCookies} cookie{totalCookies !== 1 ? "s" : ""} stored
            </>
          ) : (
            <>
              No cookies exported yet. Run{" "}
              <code>nexus cookies setup-chrome</code> to get started.
            </>
          )}
        </div>

        {domains.length > 0 && (
          <div
            style={{
              maxHeight: 200,
              overflowY: "auto",
              border: "1px solid var(--border, #333)",
              borderRadius: 6,
            }}
          >
            {domains.map((d) => (
              <div
                key={d.domain}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "6px 10px",
                  borderBottom: "1px solid var(--border, #222)",
                  fontSize: 12,
                }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {d.domain}
                  <span style={{ opacity: 0.5, marginLeft: 6 }}>
                    ({d.count})
                  </span>
                </span>
                <button
                  className="settings-btn settings-btn--danger"
                  style={{ fontSize: 11, padding: "2px 8px" }}
                  onClick={() => setDeleteTarget(d.domain)}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          style={{
            fontSize: 11,
            opacity: 0.6,
            lineHeight: 1.5,
            marginTop: 4,
          }}
        >
          <strong>Setup:</strong>{" "}
          {domains.length > 0 || loading ? (
            <>
              Run{" "}
              <code
                style={{
                  userSelect: "all",
                  padding: "1px 4px",
                  background: "var(--code-bg, var(--bg-inset))",
                  borderRadius: 3,
                }}
              >
                nexus cookies setup-chrome
              </code>{" "}
              in your terminal, then load the extension in Chrome.
            </>
          ) : (
            <>
              Load the extension in Chrome from{" "}
              <code
                style={{
                  userSelect: "all",
                  padding: "1px 4px",
                  background: "var(--code-bg, var(--bg-inset))",
                  borderRadius: 3,
                }}
              >
                ~/.nexus/extension/
              </code>{" "}
              (chrome://extensions → Developer mode → Load unpacked).
              {` Or run `}
              <code
                style={{
                  userSelect: "all",
                  padding: "1px 4px",
                  background: "var(--code-bg, var(--bg-inset))",
                  borderRadius: 3,
                }}
              >
                nexus cookies setup-chrome
              </code>{" "}
              to install the native messaging host.
            </>
          )}
        </div>
      </div>

      {deleteTarget && (
        <Modal
          kind="confirm"
          title="Remove cookies"
          message={`Delete all stored cookies for ${deleteTarget}?`}
          confirmLabel="Remove"
          danger
          onCancel={() => setDeleteTarget(null)}
          onSubmit={handleDelete}
        />
      )}
    </SettingsSection>
  );
}
