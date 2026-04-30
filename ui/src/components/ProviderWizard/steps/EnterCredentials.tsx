import { useMemo, useState } from "react";
import type { AuthMethod, CredentialPrompt, ProviderCatalogEntry } from "../../../api";
import type { FieldSchema } from "../../../types/form";
import FormRenderer from "../../FormRenderer";

interface Props {
  catalog: ProviderCatalogEntry;
  authMethod: AuthMethod;
  /** Pre-filled values (used in edit mode + when revisiting the step). */
  initialValues: Record<string, string>;
  /** Pre-filled base URL (catalog default, overridden in edit mode). */
  initialBaseUrl: string;
  /** True when editing — secret fields show a "leave blank to keep current"
   *  hint and aren't required. */
  editing: boolean;
  /** Fired on every keystroke so the parent can keep wizard state fresh
   *  (drives the Test connection button). */
  onChange: (values: Record<string, string>, baseUrl: string) => void;
  /** Called when the user clicks "Test connection". The parent runs the
   *  /providers/{name}/test request and surfaces the result. */
  onTest: () => Promise<void>;
  testResult: { ok: boolean; error: string | null; latency_ms: number } | null;
  testing: boolean;
}

/** Map a raw upstream test-connection error to something a human can act on.
 *  Falls back to the original message when no pattern matches. The full
 *  detail is dropped — it's almost always an HTTP body that includes the
 *  partially-masked API key, which we don't want to surface in the UI. */
function friendlyTestError(raw: string | null | undefined): string {
  if (!raw) return "Connection failed.";
  const lower = raw.toLowerCase();
  // Match the leading "HTTP <code>" we synthesize backend-side.
  const httpMatch = raw.match(/^HTTP\s*(\d{3})/);
  const code = httpMatch ? Number(httpMatch[1]) : null;
  if (code === 401) return "Invalid API key — check the value and try again.";
  if (code === 403) return "Key accepted but missing permission for this provider.";
  if (code === 404) return "Endpoint not found — check the Base URL.";
  if (code === 429) return "Rate limited by the provider — wait a moment and retry.";
  if (code && code >= 500) return "Provider returned a server error — try again in a minute.";
  if (lower.includes("connection refused") || lower.includes("could not reach")) {
    return "Could not reach the provider — check the Base URL and your network.";
  }
  if (lower.includes("timed out") || lower.includes("timeout")) {
    return "Request timed out — provider is slow or unreachable.";
  }
  if (lower.includes("no api key configured") || lower.includes("api_key_required")) {
    return "No API key bound to this provider yet.";
  }
  // Generic fallback — still strip anything that looks like a leaked key.
  return raw.replace(/sk-[A-Za-z0-9_-]{4,}[*A-Za-z0-9_-]*/g, "<key>").slice(0, 200);
}

function promptToFieldSchema(p: CredentialPrompt, editing: boolean): FieldSchema {
  const isSelect = p.kind === "select";
  return {
    name: p.name,
    label: p.label,
    kind: isSelect ? "select" : "text",
    required: editing && p.secret ? false : !!p.required,
    placeholder: p.placeholder ?? "",
    help: p.help ?? "",
    help_url: p.help_url ?? "",
    secret: !!p.secret,
    choices: p.choices ?? undefined,
    default: p.default ?? "",
  };
}

/** Evaluate a prompt's `when` clause against the current form values.
 *  An empty/missing `when` → always shown. */
function isPromptVisible(p: CredentialPrompt, values: Record<string, string>): boolean {
  if (!p.when) return true;
  for (const [k, v] of Object.entries(p.when)) {
    if (values[k] !== v) return false;
  }
  return true;
}

export default function EnterCredentials({
  catalog,
  authMethod,
  initialValues,
  initialBaseUrl,
  editing,
  onChange,
  onTest,
  testResult,
  testing,
}: Props) {
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [values, setValues] = useState<Record<string, string>>(initialValues);

  const visiblePrompts = useMemo(
    () => authMethod.prompts.filter((p) => isPromptVisible(p, values)),
    [authMethod.prompts, values],
  );

  const fields = useMemo(
    () => visiblePrompts.map((p) => promptToFieldSchema(p, editing)),
    [visiblePrompts, editing],
  );

  // Some catalog entries put base_url inside their prompts (Ollama, generic
  // openai-compat). Detect that — when present, the dedicated baseUrl row is
  // hidden because the prompt covers it.
  const baseUrlInPrompts = visiblePrompts.some((p) => p.name === "base_url");

  function updateValues(next: Record<string, unknown>) {
    const stringified: Record<string, string> = {};
    for (const [k, v] of Object.entries(next)) stringified[k] = v == null ? "" : String(v);
    setValues(stringified);
    onChange(stringified, baseUrlInPrompts ? (stringified.base_url ?? "") : baseUrl);
  }

  function updateBaseUrl(v: string) {
    setBaseUrl(v);
    onChange(values, v);
  }

  return (
    <div className="provider-wizard-step provider-wizard-step--creds">
      <h3 className="provider-wizard-step__title">
        {editing ? `Edit ${catalog.display_name}` : `Connect to ${catalog.display_name}`}
      </h3>

      {!baseUrlInPrompts && catalog.runtime_kind !== "anthropic" && (
        <label className="provider-wizard-field">
          <span className="provider-wizard-field__label">Base URL</span>
          <input
            className="form-input"
            value={baseUrl}
            placeholder={catalog.base_url || "https://api.example.com/v1"}
            onChange={(e) => updateBaseUrl(e.target.value)}
          />
        </label>
      )}

      <FormRenderer
        fields={fields}
        initialValues={values}
        onChange={updateValues}
        onSubmit={() => undefined}
        hideActions
      />

      <div className="provider-wizard-test-row">
        <button
          type="button"
          className="provider-wizard-test-btn"
          onClick={() => void onTest()}
          disabled={testing}
        >
          {testing ? "Testing…" : "Test connection"}
        </button>
        {testResult && testResult.ok && (
          <span className="provider-wizard-test-status provider-wizard-test-status--ok">
            ✓ Connected ({testResult.latency_ms} ms)
          </span>
        )}
        {testResult && !testResult.ok && (
          <span
            className="provider-wizard-test-status provider-wizard-test-status--err"
            title={testResult.error ?? undefined}
          >
            {friendlyTestError(testResult.error)}
          </span>
        )}
      </div>
    </div>
  );
}
