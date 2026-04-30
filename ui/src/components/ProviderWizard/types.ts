import type {
  AuthMethod,
  AuthMethodId,
  ProviderCatalogEntry,
} from "../../api";

export type WizardMode = "first-run" | "add" | "edit";

export type WizardStep =
  | "select-provider"
  | "select-auth-method"
  | "oauth-in-progress"
  | "claim-local-creds"
  | "enter-credentials"
  | "select-models";

export interface WizardState {
  step: WizardStep;
  /** Picked catalog entry. ``null`` when the user is editing a custom
   *  (non-catalog) provider in edit mode. */
  catalog: ProviderCatalogEntry | null;
  authMethod: AuthMethod | null;
  /** Provider name (key in cfg.providers). For catalog entries, defaults to
   *  ``catalog.id`` but the user can rename for custom flows. */
  providerName: string;
  baseUrl: string;
  /** Raw form values from step 3 keyed by ``CredentialPrompt.name``. */
  values: Record<string, string>;
  /** Selected models for step 4. */
  models: string[];
  testResult: { ok: boolean; error: string | null; latency_ms: number } | null;
  /** Set after the OAuth flow completes — the credential name in the
   *  secret store that the wizard then binds via ``oauth_token_ref``. */
  oauthCredentialRef: string | null;
}

export interface WizardEditPrefill {
  providerName: string;
  catalog: ProviderCatalogEntry | null;
  authMethod: AuthMethod | null;
  baseUrl: string;
  /** Existing models (just model_name strings). */
  models: string[];
  credentialRef: string | null;
}

export interface WizardCloseResult {
  /** True when the user successfully saved at least one provider. */
  saved: boolean;
}

/** Auth methods supported end-to-end by the wizard. PR 3 shipped api +
 *  anonymous; PR 4 added the two OAuth flavors; the local-creds layer
 *  added Claude Code adoption. IAM tiles still toast "coming soon" until
 *  PR 5, and ``local_codex`` ships in a follow-up. */
export const SUPPORTED_AUTH_METHODS: AuthMethodId[] = [
  "api",
  "anonymous",
  "oauth_device",
  "oauth_redirect",
  "local_claude_code",
  "local_codex",
  "iam_aws",
];

/** IAM methods route through the standard EnterCredentials step (the
 *  catalog prompts collect ``iam_profile`` + ``iam_region`` + any
 *  vendor-specific ``iam_extra.*``). The wizard payload builder splits
 *  the form values into ``iam_profile`` / ``iam_region`` / ``iam_extra``
 *  before submitting. */
export const IAM_AUTH_METHODS: AuthMethodId[] = ["iam_aws"];

export const OAUTH_AUTH_METHODS: AuthMethodId[] = [
  "oauth_device",
  "oauth_redirect",
];

/** Local-credential adoption: read auth from another tool's storage on
 *  this machine. Doesn't run an OAuth round-trip — just lifts the
 *  bundle. Treated as a separate step from oauth-in-progress because
 *  the UI text and round-trip shape differ.
 *
 *  ``local_claude_code`` lifts an OAuth bundle (refresh+access).
 *  ``local_codex`` lifts a plain API key — same step, different downstream
 *  storage shape. The wizard payload builder handles the split. */
export const LOCAL_CREDS_AUTH_METHODS: AuthMethodId[] = [
  "local_claude_code",
  "local_codex",
];

export const LOCAL_OAUTH_BUNDLE_METHODS: AuthMethodId[] = [
  "local_claude_code",
];
