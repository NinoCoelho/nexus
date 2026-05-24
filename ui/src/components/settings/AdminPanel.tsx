import { useCallback, useEffect, useState } from "react";
import {
  adminListUsers,
  adminUpdateUser,
  adminDeleteUser,
  adminListInvites,
  adminRevokeInvite,
  createInvite,
  type AdminUser,
  type AdminInvite,
} from "../../api/auth";
import { useToast } from "../../toast/ToastProvider";
import SettingsSection from "../settings/SettingsSection";
import SettingsField from "../settings/SettingsField";

export default function AdminPanel() {
  const toast = useToast();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [invites, setInvites] = useState<AdminInvite[]>([]);
  const [loading, setLoading] = useState(true);
  const [inviteRole, setInviteRole] = useState("member");
  const [creating, setCreating] = useState(false);
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [u, i] = await Promise.all([adminListUsers(), adminListInvites()]);
      setUsers(u);
      setInvites(i);
    } catch (e) {
      toast.error("Failed to load admin data", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleRoleChange(userId: string, role: string) {
    try {
      const updated = await adminUpdateUser(userId, { role });
      setUsers((prev) => prev.map((u) => (u.user_id === userId ? updated : u)));
      toast.success("Role updated");
    } catch (e) {
      toast.error("Failed to update role", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleToggleStatus(userId: string, currentStatus: string) {
    const next = currentStatus === "active" ? "suspended" : "active";
    try {
      const updated = await adminUpdateUser(userId, { status: next });
      setUsers((prev) => prev.map((u) => (u.user_id === userId ? updated : u)));
      toast.success(`User ${next}`);
    } catch (e) {
      toast.error("Failed to update status", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleDelete(userId: string, name: string) {
    if (!window.confirm(`Delete user "${name}"? This cannot be undone.`)) return;
    try {
      await adminDeleteUser(userId);
      setUsers((prev) => prev.filter((u) => u.user_id !== userId));
      toast.success("User deleted");
    } catch (e) {
      toast.error("Failed to delete user", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleCreateInvite() {
    setCreating(true);
    try {
      const inv = await createInvite({ role: inviteRole, maxUses: 1 });
      setInvites((prev) => [inv, ...prev]);
      if (inv.url) {
        setLastInviteUrl(inv.url);
        toast.success("Invite link created", { action: { label: "Copy", onClick: () => navigator.clipboard.writeText(inv.url!) } });
      } else {
        toast.success(`Invite created: ${inv.code}`);
      }
    } catch (e) {
      toast.error("Failed to create invite", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setCreating(false);
    }
  }

  async function handleRevokeInvite(code: string) {
    try {
      await adminRevokeInvite(code);
      setInvites((prev) => prev.filter((i) => i.code !== code));
      toast.success("Invite revoked");
    } catch (e) {
      toast.error("Failed to revoke invite", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  function fmtDate(ts: number | null) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleDateString();
  }

  function fmtExpiry(ts: number | null) {
    if (!ts) return "Never";
    const d = new Date(ts * 1000);
    return d < new Date() ? "Expired" : d.toLocaleDateString();
  }

  if (loading) return <p className="s-field__hint">Loading...</p>;

  return (
    <>
      <SettingsSection
        title="Users"
        icon="👥"
        description={`${users.length} registered user${users.length !== 1 ? "s" : ""}`}
      >
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Joined</th>
                <th>Last login</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id}>
                  <td>{u.display_name}</td>
                  <td className="admin-email">{u.email}</td>
                  <td>
                    <select
                      value={u.role}
                      onChange={(e) => handleRoleChange(u.user_id, e.target.value)}
                      disabled={u.role === "admin"}
                      className="admin-select"
                    >
                      <option value="admin">Admin</option>
                      <option value="member">Member</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  </td>
                  <td>
                    <button
                      type="button"
                      className={`admin-status-badge ${u.status}`}
                      onClick={() => handleToggleStatus(u.user_id, u.status)}
                      disabled={u.role === "admin"}
                    >
                      {u.status}
                    </button>
                  </td>
                  <td>{fmtDate(u.created_at)}</td>
                  <td>{fmtDate(u.last_login)}</td>
                  <td>
                    {u.role !== "admin" && (
                      <button
                        type="button"
                        className="admin-btn-danger"
                        onClick={() => handleDelete(u.user_id, u.display_name)}
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SettingsSection>

      <SettingsSection
        title="Invites"
        icon="✉️"
        description="Create and manage invite links"
      >
        <SettingsField label="Create invite" layout="row">
          <div className="admin-invite-row">
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              className="admin-select"
            >
              <option value="member">Member</option>
              <option value="viewer">Viewer</option>
              <option value="admin">Admin</option>
            </select>
            <button
              type="button"
              className="settings-btn"
              disabled={creating}
              onClick={() => handleCreateInvite()}
            >
              {creating ? "Creating..." : "Create"}
            </button>
          </div>
        </SettingsField>
        {lastInviteUrl && (
          <div className="admin-invite-url">
            <input
              type="text"
              readOnly
              value={lastInviteUrl}
              className="admin-invite-url-input"
              onFocus={(e) => e.target.select()}
            />
            <button
              type="button"
              className="settings-btn"
              onClick={() => {
                navigator.clipboard.writeText(lastInviteUrl);
                toast.success("Copied!");
              }}
            >
              Copy
            </button>
          </div>
        )}
        {invites.length > 0 && (
          <div className="admin-table-wrap">
            <table className="admin-table admin-table-sm">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Role</th>
                  <th>Uses</th>
                  <th>Expires</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {invites.map((inv) => (
                  <tr key={inv.code}>
                    <td className="admin-mono">{inv.code}</td>
                    <td>{inv.role}</td>
                    <td>{inv.max_uses}</td>
                    <td>{fmtExpiry(inv.expires_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="admin-btn-danger"
                        onClick={() => handleRevokeInvite(inv.code)}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SettingsSection>
    </>
  );
}
