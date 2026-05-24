// Bundled provider catalog + wizard endpoints.
import { BASE } from "./base";

export type AuthMethodId =
  | "api"
  | "oauth_device"
  | "oauth_redirect"
  | "iam_aws"
  | "iam_gcp"
  | "iam_azure"
  | "anonymous"
  | "local_claude_code"
  | "local_codex"
  | "nexus_signin";

export type RuntimeKind =
  | "openai_compat"
  | "anthropic"
  | "ollama"
  | "bedrock"
  | "vertex"
  | "azure_openai"
  | "nexus";

export type ProviderCategory =
  | "frontier"
  | "open"
  | "cloud"
  | "local"
  | "aggregator"
  | "other";

export interface CredentialPrompt {
  name: string;
  label: string;
  kind: "text" | "password" | "select";
  placeholder?: string;
  help?: string;
  help_url?: string;
  required?: boolean;
  secret?: boolean;
  choices?: string[] | null;
  default?: string;
  /** Show this field only when other form values match. e.g. {"region_kind":"custom"}. */
  when?: Record<string, string> | null;
}

export interface OAuthSpec {
  flavor: "device" | "redirect";
  client_id?: string;
  auth_url?: string;
  token_url?: string;
  device_url?: string;
  scopes?: string[];
  redirect_path?: string;
  pkce?: boolean;
}

export interface AuthMethod {
  id: AuthMethodId;
  label: string;
  priority: number;
  prompts: CredentialPrompt[];
  oauth?: OAuthSpec | null;
  /** Optional pip extra required at runtime (e.g. "bedrock"). */
  requires_extra?: string;
}

export type ModelCapability =
  | "chat"
  | "tools"
  | "reasoning"
  | "vision"
  | "audio"
  | "embedding";

export interface ModelInfo {
  id: string;
  capabilities: ModelCapability[];
  context_window?: number;
}

export interface ProviderCatalogEntry {
  id: string;
  display_name: string;
  category: ProviderCategory;
  runtime_kind: RuntimeKind;
  base_url?: string;
  base_url_template?: string;
  env_var_names: string[];
  auth_methods: AuthMethod[];
  default_models: ModelInfo[];
  docs_url?: string;
  icon?: string;
  /** Pinned at the top of the wizard's provider picker in its own
   *  section. Used to surface the Nexus subscription. */
  featured?: boolean;
  /** One-line tagline shown beside featured tiles. */
  tagline?: string;
}

export async function fetchProviderCatalog(): Promise<ProviderCatalogEntry[]> {
  const res = await fetch(`${BASE}/catalog/providers`);
  if (!res.ok) throw new Error(`Catalog error: ${res.status}`);
  return res.json();
}

// ── wizard apply / test ──────────────────────────────────────────────────────

export interface WizardPayload {
  name: string;
  catalog_id: string | null;
  auth_method_id: AuthMethodId;
  runtime_kind: RuntimeKind;
  base_url: string;
  credential_ref: string | null;
  /** Credential names + values to write into the secret store. May be empty
   *  when the user picked an already-stored credential for `credential_ref`. */
  credentials: Record<string, string>;
  iam_profile?: string;
  iam_region?: string;
  iam_extra?: Record<string, string>;
  models: string[];
}

export interface WizardResult {
  name: string;
  catalog_id: string | null;
  runtime_kind: string;
  auth_kind: string;
  base_url: string;
  credential_ref: string | null;
  models: string[];
}

export async function applyProviderWizard(
  payload: WizardPayload,
): Promise<WizardResult> {
  const res = await fetch(`${BASE}/providers/wizard`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Wizard error: ${res.status}`);
  }
  return res.json();
}

export interface ProviderTestResult {
  ok: boolean;
  error: string | null;
  latency_ms: number;
}

export async function testProviderConnection(
  name: string,
): Promise<ProviderTestResult> {
  const res = await fetch(
    `${BASE}/providers/${encodeURIComponent(name)}/test`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Test connection error: ${res.status}`);
  return res.json();
}

// ── OAuth (PR 4) ────────────────────────────────────────────────────────────

export interface OAuthDeviceStartResult {
  session_id: string;
  flow: "device";
  verification_uri: string;
  user_code: string;
  interval: number;
}

export interface OAuthRedirectStartResult {
  session_id: string;
  flow: "redirect";
  authorize_url: string;
}

export type OAuthStartResult = OAuthDeviceStartResult | OAuthRedirectStartResult;

export async function startOAuthFlow(
  catalogId: string,
  authMethodId: AuthMethodId,
): Promise<OAuthStartResult> {
  const res = await fetch(`${BASE}/auth/oauth/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      catalog_id: catalogId,
      auth_method_id: authMethodId,
    }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `OAuth start error: ${res.status}`);
  }
  return res.json();
}

export interface OAuthPollResult {
  status: "pending" | "ok" | "error";
  credential_ref?: string;
  error?: string;
}

export async function pollOAuthFlow(sessionId: string): Promise<OAuthPollResult> {
  const res = await fetch(`${BASE}/auth/oauth/poll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (res.status === 404) {
    return { status: "error", error: "session expired or not found" };
  }
  if (!res.ok) throw new Error(`OAuth poll error: ${res.status}`);
  return res.json();
}

// ── Local-credential adoption ───────────────────────────────────────────────

export interface ClaimLocalCredsResult {
  credential_ref: string;
  subscription?: string | null;
  expires_at?: number;
}

/** Lift the OAuth bundle that ``claude-code`` stored in the OS keychain
 *  and persist it under ``ANTHROPIC_CLAUDE_CODE`` in our secrets store.
 *  Loopback-only on the server. */
export async function claimClaudeCodeCredentials(): Promise<ClaimLocalCredsResult> {
  const res = await fetch(`${BASE}/auth/local/claude-code/claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Claim failed: ${res.status}`);
  }
  return res.json();
}

export interface ClaimCodexResult {
  credential_ref: string;
  auth_mode?: string;
}

/** Lift the OPENAI_API_KEY ``codex`` already has on disk and persist it
 *  under ``OPENAI_CODEX_LOCAL`` in our secrets store. Refuses
 *  ChatGPT-mode tokens (those don't work against api.openai.com). */
export async function claimCodexCredentials(): Promise<ClaimCodexResult> {
  const res = await fetch(`${BASE}/auth/local/codex/claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Claim failed: ${res.status}`);
  }
  return res.json();
}
