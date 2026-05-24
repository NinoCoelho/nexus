import { BASE } from "./base";

export interface AuthStatus {
  multi_user: boolean;
  needs_setup?: boolean;
  setup_token_required?: boolean;
  authenticated?: boolean;
  user_id?: string;
  email?: string;
  display_name?: string;
  role?: string;
  has_password?: boolean;
}

export interface SessionInfo {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  has_password?: boolean;
}

export async function probeAuthStatus(): Promise<AuthStatus> {
  try {
    const res = await fetch(`${BASE}/auth/status`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) return { multi_user: false };
    return await res.json();
  } catch {
    return { multi_user: false };
  }
}

export async function setupAccount(
  token: string,
  email: string,
  displayName: string,
): Promise<SessionInfo> {
  const res = await fetch(`${BASE}/auth/setup`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, email, display_name: displayName }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Setup failed";
    throw new Error(detail);
  }
  const data = await res.json();
  return {
    user_id: data.user_id,
    email: data.email ?? email,
    display_name: data.display_name ?? displayName,
    role: data.role ?? "admin",
  };
}

export async function getInviteInfo(code: string): Promise<{
  code: string;
  email: string | null;
  role: string;
}> {
  const res = await fetch(`${BASE}/auth/invite/${encodeURIComponent(code)}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Invalid invite";
    throw new Error(detail);
  }
  return res.json();
}

export async function registerWithInvite(
  code: string,
  email: string,
  displayName: string,
  password?: string,
): Promise<SessionInfo> {
  const body: Record<string, string> = { code, email, display_name: displayName };
  if (password) body.password = password;
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Registration failed";
    throw new Error(detail);
  }
  const data = await res.json();
  return {
    user_id: data.user_id,
    email: data.email ?? email,
    display_name: data.display_name ?? displayName,
    role: data.role ?? "member",
  };
}

export async function loginWithEmail(
  email: string,
  password: string,
): Promise<SessionInfo> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Login failed";
    throw new Error(detail);
  }
  const data = await res.json();
  return {
    user_id: data.user_id,
    email: data.email,
    display_name: data.display_name,
    role: data.role,
  };
}

export async function setPassword(password: string): Promise<void> {
  const res = await fetch(`${BASE}/auth/set-password`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Failed to set password";
    throw new Error(detail);
  }
}

export async function getSession(): Promise<SessionInfo> {
  const res = await fetch(`${BASE}/auth/session`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Not authenticated");
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
  });
}

export async function refreshSession(): Promise<SessionInfo> {
  const res = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Session refresh failed");
  return res.json();
}

export async function changeDisplayName(name: string): Promise<SessionInfo> {
  const res = await fetch(`${BASE}/auth/change-name`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: name }),
  });
  if (!res.ok) throw new Error("Failed to update name");
  return res.json();
}

export async function createInvite(opts: {
  email?: string;
  role?: string;
  maxUses?: number;
  expires_in_hours?: number;
}): Promise<{ code: string; url: string | null; email: string | null; role: string; max_uses: number; expires_at: number | null }> {
  const res = await fetch(`${BASE}/auth/invites`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: opts.email || null,
      role: opts.role || "member",
      max_uses: opts.maxUses || 1,
      expires_in_hours: opts.expires_in_hours ?? null,
    }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Failed to create invite";
    throw new Error(detail);
  }
  return res.json();
}

export async function enableMultiUser(): Promise<{
  enabled: boolean;
  message: string;
}> {
  const res = await fetch(`${BASE}/config/enable-multi-user`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Failed to enable";
    throw new Error(detail);
  }
  return res.json();
}

export interface AdminUser {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
  created_at: number;
  last_login: number | null;
}

export interface AdminInvite {
  code: string;
  url: string | null;
  email: string | null;
  role: string;
  max_uses: number;
  expires_at: number | null;
}

export async function adminListUsers(): Promise<AdminUser[]> {
  const res = await fetch(`${BASE}/auth/admin/users`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to list users");
  return res.json();
}

export async function adminUpdateUser(
  userId: string,
  updates: { role?: string; status?: string; display_name?: string },
): Promise<AdminUser> {
  const res = await fetch(`${BASE}/auth/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Failed to update user";
    throw new Error(detail);
  }
  return res.json();
}

export async function adminDeleteUser(userId: string): Promise<void> {
  const res = await fetch(`${BASE}/auth/admin/users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Failed to delete user";
    throw new Error(detail);
  }
}

export async function adminListInvites(): Promise<AdminInvite[]> {
  const res = await fetch(`${BASE}/auth/invites`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to list invites");
  return res.json();
}

export async function adminRevokeInvite(code: string): Promise<void> {
  const res = await fetch(`${BASE}/auth/invites/${encodeURIComponent(code)}`, {
    method: "DELETE",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to revoke invite");
}

export interface AdminPendingItem {
  session_id: string;
  request_id: string;
  prompt: string;
  kind: string;
  status: string;
  user_id?: string;
  user_name?: string;
  choices?: string[];
  timeout_seconds?: number;
}

export async function adminAllPending(): Promise<AdminPendingItem[]> {
  const res = await fetch(`${BASE}/admin/pending`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch admin pending");
  const data = await res.json();
  return data.pending;
}

export async function adminAnswerHitl(
  sessionId: string,
  requestId: string,
  answer: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/admin/hitl/${encodeURIComponent(sessionId)}/${encodeURIComponent(requestId)}/answer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
      credentials: "include",
      cache: "no-store",
    },
  );
  if (!res.ok) throw new Error("Failed to answer HITL request");
}

export async function adminCancelHitl(
  sessionId: string,
  requestId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/admin/hitl/${encodeURIComponent(sessionId)}/${encodeURIComponent(requestId)}/cancel`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
    },
  );
  if (!res.ok) throw new Error("Failed to cancel HITL request");
}

export interface SharedResource {
  id: string;
  path: string;
  type: string;
  owner_id: string;
  owner_name?: string;
  size?: number;
  created_at: number;
}

export async function listSharedResources(): Promise<SharedResource[]> {
  const res = await fetch(`${BASE}/vault/shared`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to list shared resources");
  const data = await res.json();
  return data.resources;
}

export async function shareVaultResource(opts: {
  path: string;
  grantee_type?: string;
  grantee_id?: string;
  access_level?: string;
}): Promise<{ id: string; path: string; status: string }> {
  const res = await fetch(`${BASE}/vault/shared`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || "Failed to share resource");
  }
  return res.json();
}

export async function unshareVaultResource(path: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/shared/${encodeURIComponent(path)}`, {
    method: "DELETE",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to unshare resource");
}

export async function getSharedAcl(
  path: string,
): Promise<{ acl: AclEntry[] }> {
  const res = await fetch(
    `${BASE}/vault/shared/${encodeURIComponent(path)}/acl`,
    { credentials: "include", cache: "no-store" },
  );
  if (!res.ok) throw new Error("Failed to get ACL");
  return res.json();
}

export async function updateSharedAcl(
  path: string,
  entries: AclUpdate[],
): Promise<void> {
  const res = await fetch(
    `${BASE}/vault/shared/${encodeURIComponent(path)}/acl`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries }),
      credentials: "include",
      cache: "no-store",
    },
  );
  if (!res.ok) throw new Error("Failed to update ACL");
}

export interface AclEntry {
  id: string;
  resource_path: string;
  grantee_type: string;
  grantee_id: string;
  access_level: string;
  granted_by: string;
  granted_at: number;
}

export interface AclUpdate {
  grantee_type: string;
  grantee_id: string;
  access_level?: string;
  remove?: boolean;
}
