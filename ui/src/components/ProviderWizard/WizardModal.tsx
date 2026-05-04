/**
 * WizardModal — unified provider onboarding wizard.
 *
 * Three modes share the same step machine:
 *   - "first-run": auto-opened by App when no provider is configured.
 *     Non-dismissible until the user saves at least one provider+model.
 *   - "add": dismissible; opened from the "+ Add provider" button.
 *   - "edit": dismissible; entry point skips steps 1 + 2 and lands on
 *     the credentials step with the provider's current values.
 *
 * Per CLAUDE.md, no native dialogs (alert/confirm/prompt) — confirmations
 * use Modal + useToast.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyProviderWizard,
  fetchProviderCatalog,
  fetchProviderModels,
  getConfig,
  getNexusAccountStatus,
  testProviderConnection,
  type AuthMethod,
  type ProviderCatalogEntry,
  type WizardPayload,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import "./WizardModal.css";
import ClaimLocalCreds from "./steps/ClaimLocalCreds";
import NexusSignin from "./steps/NexusSignin";
import OAuthInProgress from "./steps/OAuthInProgress";
import SelectAuthMethod from "./steps/SelectAuthMethod";
import SelectModels from "./steps/SelectModels";
import SelectProvider from "./steps/SelectProvider";
import EnterCredentials from "./steps/EnterCredentials";
import type {
  WizardCloseResult,
  WizardEditPrefill,
  WizardMode,
  WizardState,
  WizardStep,
} from "./types";
import {
  IAM_AUTH_METHODS,
  LOCAL_CREDS_AUTH_METHODS,
  LOCAL_OAUTH_BUNDLE_METHODS,
  NEXUS_AUTH_METHODS,
  OAUTH_AUTH_METHODS,
  SUPPORTED_AUTH_METHODS,
} from "./types";

interface Props {
  mode: WizardMode;
  /** Provider names already configured. Surfaced in step 1 so the user can
   *  see which entries are fresh vs revisited. */
  configuredNames: string[];
  /** When `mode === "edit"`, the existing provider state to seed from. */
  editPrefill?: WizardEditPrefill;
  onClose: (result: WizardCloseResult) => void;
}

const DISMISSIBLE_MODES: WizardMode[] = ["add", "edit"];

function emptyState(): WizardState {
  return {
    step: "select-provider",
    catalog: null,
    authMethod: null,
    providerName: "",
    baseUrl: "",
    values: {},
    models: [],
    testResult: null,
    oauthCredentialRef: null,
    selectedCredentialRef: null,
  };
}

/** Initial form values for a credential prompt set: catalog defaults +
 *  prefill (in edit mode, the existing base_url etc. — secrets are never
 *  prefilled). */
function seedValues(authMethod: AuthMethod): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of authMethod.prompts) {
    if (p.default) out[p.name] = p.default;
  }
  return out;
}

export default function WizardModal({
  mode,
  configuredNames,
  editPrefill,
  onClose,
}: Props) {
  const toast = useToast();
  const dismissible = DISMISSIBLE_MODES.includes(mode);
  const [catalog, setCatalog] = useState<ProviderCatalogEntry[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [state, setState] = useState<WizardState>(() => emptyState());
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [nexusWebsiteUrl, setNexusWebsiteUrl] = useState<string>(
    "https://www.nexus-model.us",
  );

  // Pull the configured Nexus website URL once so the nexus_signin step
  // can target the right host (defaulting to production).
  useEffect(() => {
    getConfig()
      .then((cfg) => {
        if (cfg.nexus_account?.base_url) setNexusWebsiteUrl(cfg.nexus_account.base_url);
      })
      .catch(() => {
        // Non-fatal — the default URL still works in production.
      });
  }, []);

  // Seed catalog + edit-mode prefill on first render.
  useEffect(() => {
    let cancelled = false;
    fetchProviderCatalog()
      .then((entries) => {
        if (cancelled) return;
        setCatalog(entries);
        if (mode === "edit" && editPrefill) {
          // Edit mode: jump to step 3 prefilled. authMethod is locked
          // (changing auth = remove + add).
          const c = editPrefill.catalog ?? entries.find((e) => e.id === editPrefill.providerName) ?? null;
          const m =
            editPrefill.authMethod ??
            (c ? c.auth_methods.find((am) => SUPPORTED_AUTH_METHODS.includes(am.id)) ?? null : null);
          setState({
            step: "enter-credentials",
            catalog: c,
            authMethod: m,
            providerName: editPrefill.providerName,
            baseUrl: editPrefill.baseUrl,
            values: m ? seedValues(m) : {},
            models: editPrefill.models,
            testResult: null,
            oauthCredentialRef: editPrefill.credentialRef,
            selectedCredentialRef: null,
          });
        }
      })
      .catch((e) => {
        if (!cancelled) setCatalogError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [mode, editPrefill]);

  // Escape closes — only when dismissible.
  useEffect(() => {
    if (!dismissible) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose({ saved: false });
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [dismissible, onClose]);

  const advanceToMethod = useCallback(
    (entry: ProviderCatalogEntry, m: AuthMethod) => {
      const nextStep: WizardStep = NEXUS_AUTH_METHODS.includes(m.id)
        ? "nexus-signin"
        : OAUTH_AUTH_METHODS.includes(m.id)
          ? "oauth-in-progress"
          : LOCAL_CREDS_AUTH_METHODS.includes(m.id)
            ? "claim-local-creds"
            : "enter-credentials";
      setState((prev) => ({
        ...prev,
        step: nextStep,
        catalog: entry,
        authMethod: m,
        providerName: entry.id,
        baseUrl: entry.base_url ?? "",
        values: seedValues(m),
        models: [],
        testResult: null,
        oauthCredentialRef: null,
        selectedCredentialRef: null,
      }));
    },
    [],
  );

  const handlePickProvider = useCallback(
    (entry: ProviderCatalogEntry) => {
      // If only one supported method, skip step 2.
      const supported = entry.auth_methods.filter((m) =>
        SUPPORTED_AUTH_METHODS.includes(m.id),
      );
      if (entry.auth_methods.length === 1 && supported.length === 1) {
        advanceToMethod(entry, entry.auth_methods[0]);
        return;
      }
      setState((prev) => ({
        ...prev,
        step: "select-auth-method",
        catalog: entry,
        providerName: entry.id,
        baseUrl: entry.base_url ?? "",
      }));
    },
    [advanceToMethod],
  );

  const handlePickAuthMethod = useCallback(
    (m: AuthMethod) => {
      setState((prev) => {
        if (!prev.catalog) return prev;
        const nextStep: WizardStep = NEXUS_AUTH_METHODS.includes(m.id)
          ? "nexus-signin"
          : OAUTH_AUTH_METHODS.includes(m.id)
            ? "oauth-in-progress"
            : LOCAL_CREDS_AUTH_METHODS.includes(m.id)
              ? "claim-local-creds"
              : "enter-credentials";
        return {
          ...prev,
          step: nextStep,
          authMethod: m,
          values: seedValues(m),
          models: [],
          testResult: null,
          oauthCredentialRef: null,
          selectedCredentialRef: null,
        };
      });
    },
    [],
  );

  const handleOAuthComplete = useCallback((credentialRef: string) => {
    setState((prev) => ({
      ...prev,
      step: "select-models",
      oauthCredentialRef: credentialRef,
    }));
  }, []);

  // ── Nexus subscription auto-apply ──────────────────────────────────
  // The popup just stored the apiKey via /auth/nexus/verify. We skip
  // the model-picker step (the catalog already declares demo + nexus)
  // and apply the wizard payload immediately so the user lands back
  // in chat.
  //
  // ``buildPayloadRef`` breaks the forward-reference: handleNexusSignedIn
  // is built *before* buildPayload (because state setters are declared
  // top-down), so we read the latest function from a ref that gets
  // populated below in an effect.
  const buildPayloadRef = useRef<((s: WizardState) => WizardPayload | null) | null>(null);
  const handleNexusSignedIn = useCallback(async (payload?: { tier: string; apiKey: string }) => {
    // Resolve the canonical model for the tier. Pro users get nexus model,
    // free users get demo model.
    setSubmitting(true);
    let canonical = "demo";

    // Use tier from callback if available (pro = nexus, free = demo)
    if (payload?.tier === "pro") {
      canonical = "nexus";
    } else if (payload?.tier === "free") {
      canonical = "demo";
    } else {
      // Fallback: fetch live status for models list
      try {
        const live = await getNexusAccountStatus();
        const models = live.status?.models ?? live.models ?? [];
        if (models.includes("nexus")) canonical = "nexus";
        else if (models.includes("demo")) canonical = "demo";
      } catch {
        // Fall through to demo — the watcher will reconcile to the right
        // model on its next tick anyway.
      }
    }
    setState((prev) => {
      if (!prev.catalog || !prev.authMethod) {
        setSubmitting(false);
        return prev;
      }
      const next = { ...prev, models: [canonical] };
      void (async () => {
        try {
          const build = buildPayloadRef.current;
          const payload = build ? build(next) : null;
          if (!payload) {
            toast.error("Could not build wizard payload after sign-in.");
            return;
          }
          await applyProviderWizard(payload);
          toast.success(`Connected ${payload.name}.`);
          onClose({ saved: true });
        } catch (e) {
          toast.error("Save failed after sign-in.", {
            detail: e instanceof Error ? e.message : undefined,
          });
        } finally {
          setSubmitting(false);
        }
      })();
      return next;
    });
  }, [onClose, toast]);

  const handleUnsupportedAuthMethod = useCallback(
    (_m: AuthMethod) => {
      toast.info("IAM sign-in ships in a later release.", {
        detail: "For now, pick another auth method or use a different provider.",
      });
    },
    [toast],
  );

  const handleCredentialsChange = useCallback(
    (values: Record<string, string>, baseUrl: string, selectedCredentialRef: string | null) => {
      setState((prev) => ({
        ...prev,
        values,
        baseUrl,
        testResult: null,
        selectedCredentialRef,
      }));
    },
    [],
  );

  const handleModelsChange = useCallback((models: string[]) => {
    setState((prev) => ({ ...prev, models }));
  }, []);

  const buildPayload = useCallback((s: WizardState): WizardPayload | null => {
    if (!s.catalog || !s.authMethod) return null;
    const catalog = s.catalog;
    const authMethod = s.authMethod;
    const credentials: Record<string, string> = {};
    let credentialRef: string | null = null;
    let baseUrl = s.baseUrl;
    let iamProfile = "";
    let iamRegion = "";
    const iamExtra: Record<string, string> = {};

    if (NEXUS_AUTH_METHODS.includes(authMethod.id)) {
      // Nexus subscription: backend stored the apiKey under
      // "nexus_api_key" via /auth/nexus/verify. Just bind the provider
      // entry to that name; never pass credential values from the UI.
      credentialRef = "nexus_api_key";
    } else if (authMethod.id === "anonymous") {
      // base_url may be carried in s.values["base_url"] OR s.baseUrl.
      baseUrl = s.values.base_url || baseUrl;
    } else if (
      OAUTH_AUTH_METHODS.includes(authMethod.id)
      || LOCAL_OAUTH_BUNDLE_METHODS.includes(authMethod.id)
    ) {
      // True OAuth bundles (refresh+access). Backend stores via
      // oauth_token_ref under auth_kind=oauth.
      credentialRef = s.oauthCredentialRef;
    } else if (LOCAL_CREDS_AUTH_METHODS.includes(authMethod.id)) {
      // Local API-key adoption (e.g. Codex). The claim endpoint already
      // wrote the key as a regular credential; the backend treats this
      // path as auth_kind=api with credential_ref pointing at the name.
      credentialRef = s.oauthCredentialRef;
    } else if (IAM_AUTH_METHODS.includes(authMethod.id)) {
      // IAM (currently only iam_aws). Catalog prompts collected
      // iam_profile + iam_region (+ iam_extra.* for vendor-specific).
      // Split dotted prompt names into the iam_extra dict.
      iamProfile = s.values.iam_profile ?? "";
      iamRegion = s.values.iam_region ?? "";
      for (const [k, v] of Object.entries(s.values)) {
        if (k.startsWith("iam_extra.") && v) {
          iamExtra[k.slice("iam_extra.".length)] = v;
        }
      }
    } else if (authMethod.id === "api") {
      if (s.selectedCredentialRef) {
        credentialRef = s.selectedCredentialRef;
      } else if (s.values.credential_name && s.values.credential_value) {
        credentialRef = s.values.credential_name;
        credentials[s.values.credential_name] = s.values.credential_value;
      } else {
        const secretPrompt = authMethod.prompts.find((p) => p.secret);
        if (secretPrompt) {
          credentialRef = secretPrompt.name;
          const v = s.values[secretPrompt.name];
          if (v) credentials[secretPrompt.name] = v;
        }
      }
      if (s.values.base_url) baseUrl = s.values.base_url;
    }

    return {
      name: s.providerName,
      catalog_id: catalog.id,
      auth_method_id: authMethod.id,
      runtime_kind: catalog.runtime_kind,
      base_url: baseUrl,
      credential_ref: credentialRef,
      credentials,
      models: s.models,
      iam_profile: iamProfile,
      iam_region: iamRegion,
      iam_extra: iamExtra,
    };
  }, []);

  // Bind the latest buildPayload into the ref so handleNexusSignedIn
  // (declared above for ordering) can call it without a circular
  // dependency.
  useEffect(() => {
    buildPayloadRef.current = buildPayload;
  }, [buildPayload]);

  const handleTest = useCallback(async () => {
    if (!state.catalog || !state.authMethod) return;
    const payload = buildPayload(state);
    if (!payload) return;
    setTesting(true);
    try {
      // Test happens AFTER apply — we save first, then probe. This means
      // "Test connection" shares state with the saved provider, which is
      // simpler than maintaining a transient probe path on the backend.
      // Side-effect of click: provider is created/updated even if the
      // user later cancels — acceptable trade-off because cancelling
      // mid-wizard is rare.
      await applyProviderWizard(payload);
      const res = await testProviderConnection(state.providerName);
      setState((prev) => ({ ...prev, testResult: res }));
      if (!res.ok) {
        toast.error("Connection failed.", { detail: res.error ?? undefined });
      }
    } catch (e) {
      toast.error("Test failed.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setTesting(false);
    }
  }, [state, buildPayload, toast]);

  const handleDiscover = useCallback(async (): Promise<string[]> => {
    if (!state.providerName) return [];
    setDiscovering(true);
    try {
      const res = await fetchProviderModels(state.providerName);
      if (!res.ok) {
        toast.warning("Provider model list unavailable.", {
          detail: res.error ?? undefined,
        });
        return [];
      }
      return res.models;
    } finally {
      setDiscovering(false);
    }
  }, [state.providerName, toast]);

  const handleNext = useCallback(() => {
    setState((prev) => {
      if (prev.step === "enter-credentials") return { ...prev, step: "select-models" };
      return prev;
    });
  }, []);

  const handleBack = useCallback(() => {
    setState((prev) => {
      if (prev.step === "select-models") {
        // Back from step 4 returns to whichever step 3 variant we came
        // from. Each auth flavor has its own step 3 alternate.
        if (prev.authMethod && OAUTH_AUTH_METHODS.includes(prev.authMethod.id)) {
          return { ...prev, step: "oauth-in-progress" };
        }
        if (prev.authMethod && LOCAL_CREDS_AUTH_METHODS.includes(prev.authMethod.id)) {
          return { ...prev, step: "claim-local-creds" };
        }
        return { ...prev, step: "enter-credentials" };
      }
      if (
        prev.step === "enter-credentials"
        || prev.step === "oauth-in-progress"
        || prev.step === "claim-local-creds"
        || prev.step === "nexus-signin"
      ) {
        // In edit mode, back from credentials closes the wizard — there's
        // no step 1/2 to return to.
        if (mode === "edit") {
          onClose({ saved: false });
          return prev;
        }
        // Skip step 2 if there was only one supported method and we
        // didn't actually visit it.
        if (prev.catalog && prev.catalog.auth_methods.length === 1) {
          return { ...prev, step: "select-provider" };
        }
        return { ...prev, step: "select-auth-method" };
      }
      if (prev.step === "select-auth-method") return { ...prev, step: "select-provider" };
      return prev;
    });
  }, [mode, onClose]);

  const handleSave = useCallback(async () => {
    const payload = buildPayload(state);
    if (!payload) return;
    if (payload.models.length === 0) {
      toast.warning("Pick at least one model before saving.");
      return;
    }
    setSubmitting(true);
    try {
      await applyProviderWizard(payload);
      toast.success(
        mode === "edit"
          ? `Updated ${payload.name}.`
          : `Added ${payload.name} with ${payload.models.length} model${payload.models.length === 1 ? "" : "s"}.`,
      );
      onClose({ saved: true });
    } catch (e) {
      toast.error("Save failed.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSubmitting(false);
    }
  }, [state, buildPayload, mode, onClose, toast]);

  const stepIndex = useMemo<number>(() => {
    // OAuth, local-creds, and the credentials step are all step-3
    // alternates depending on auth method.
    const map: Record<WizardStep, number> = {
      "select-provider": 1,
      "select-auth-method": 2,
      "enter-credentials": 3,
      "oauth-in-progress": 3,
      "claim-local-creds": 3,
      "nexus-signin": 3,
      "select-models": 4,
    };
    return map[state.step];
  }, [state.step]);

  if (!catalog && !catalogError) {
    return (
      <div className="provider-wizard-overlay">
        <div className="provider-wizard-panel">
          <p className="provider-wizard-loading">Loading provider catalog…</p>
        </div>
      </div>
    );
  }

  if (catalogError) {
    return (
      <div className="provider-wizard-overlay">
        <div className="provider-wizard-panel">
          <p className="provider-wizard-error">
            Could not load catalog: {catalogError}
          </p>
          {dismissible && (
            <div className="provider-wizard-footer">
              <button
                type="button"
                className="provider-wizard-secondary-btn"
                onClick={() => onClose({ saved: false })}
              >
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className="provider-wizard-overlay"
      onClick={(e) => {
        if (dismissible && e.target === e.currentTarget) {
          onClose({ saved: false });
        }
      }}
    >
      <div className="provider-wizard-panel" role="dialog" aria-modal="true">
        <div className="provider-wizard-header">
          <span className="provider-wizard-title">
            {mode === "first-run" && "Welcome to Nexus — let's set up an LLM provider"}
            {mode === "add" && "Add an LLM provider"}
            {mode === "edit" && state.catalog
              ? `Edit ${state.catalog.display_name}`
              : ""}
          </span>
          <span className="provider-wizard-step-counter">
            Step {stepIndex} of {mode === "edit" ? 2 : 4}
          </span>
          {dismissible && (
            <button
              type="button"
              className="provider-wizard-close"
              onClick={() => onClose({ saved: false })}
              aria-label="Close wizard"
            >
              ✕
            </button>
          )}
        </div>

        <div className="provider-wizard-body">
          {state.step === "select-provider" && catalog && (
            <SelectProvider
              catalog={catalog}
              configuredNames={configuredNames}
              onPick={handlePickProvider}
            />
          )}
          {state.step === "select-auth-method" && state.catalog && (
            <SelectAuthMethod
              catalog={state.catalog}
              onPick={handlePickAuthMethod}
              onUnsupported={handleUnsupportedAuthMethod}
            />
          )}
          {state.step === "enter-credentials" && state.catalog && state.authMethod && (
            <EnterCredentials
              catalog={state.catalog}
              authMethod={state.authMethod}
              initialValues={state.values}
              initialBaseUrl={state.baseUrl}
              editing={mode === "edit"}
              selectedCredentialRef={state.selectedCredentialRef}
              onChange={handleCredentialsChange}
              onTest={handleTest}
              testResult={state.testResult}
              testing={testing}
            />
          )}
          {state.step === "oauth-in-progress" && state.catalog && state.authMethod && (
            <OAuthInProgress
              catalog={state.catalog}
              authMethod={state.authMethod}
              onComplete={handleOAuthComplete}
              onCancel={handleBack}
            />
          )}
          {state.step === "claim-local-creds" && state.catalog && state.authMethod && (
            <ClaimLocalCreds
              catalog={state.catalog}
              authMethod={state.authMethod}
              onComplete={handleOAuthComplete}
              onCancel={handleBack}
            />
          )}
          {state.step === "nexus-signin" && (
            <NexusSignin
              websiteUrl={nexusWebsiteUrl}
              onSignedIn={(payload) => void handleNexusSignedIn(payload)}
              onCancel={handleBack}
              busy={submitting}
            />
          )}
          {state.step === "select-models" && state.catalog && (
            <SelectModels
              catalog={state.catalog}
              selected={state.models}
              onChange={handleModelsChange}
              onDiscover={handleDiscover}
              discovering={discovering}
            />
          )}
        </div>

        <div className="provider-wizard-footer">
          {state.step !== "select-provider"
            && state.step !== "oauth-in-progress"
            && state.step !== "claim-local-creds"
            && state.step !== "nexus-signin" && (
              <button
                type="button"
                className="provider-wizard-secondary-btn"
                onClick={handleBack}
                disabled={submitting}
              >
                Back
              </button>
            )}
          <span style={{ flex: 1 }} />
          {state.step === "enter-credentials" && (
            <button
              type="button"
              className="provider-wizard-primary-btn"
              onClick={handleNext}
              disabled={submitting}
            >
              Next
            </button>
          )}
          {/* OAuth step has no "Next" — it auto-advances when the upstream
              flow completes. Cancel lives on the step card itself. */}
          {state.step === "select-models" && (
            <button
              type="button"
              className="provider-wizard-primary-btn"
              onClick={() => void handleSave()}
              disabled={submitting || state.models.length === 0}
            >
              {submitting ? "Saving…" : mode === "edit" ? "Save changes" : "Save provider"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
