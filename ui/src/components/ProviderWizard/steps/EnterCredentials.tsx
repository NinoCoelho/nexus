import { useEffect, useMemo, useState } from "react";
import {
  listCredentials,
  type Credential,
  type AuthMethod,
  type CredentialPrompt,
  type ProviderCatalogEntry,
} from "../../../api";
import type { FieldSchema } from "../../../types/form";
import FormRenderer from "../../FormRenderer";

interface Props {
  catalog: ProviderCatalogEntry;
  authMethod: AuthMethod;
  initialValues: Record<string, string>;
  initialBaseUrl: string;
  editing: boolean;
  selectedCredentialRef: string | null;
  onChange: (
    values: Record<string, string>,
    baseUrl: string,
    selectedCredentialRef: string | null,
  ) => void;
  onTest: () => Promise<void>;
  testResult: { ok: boolean; error: string | null; latency_ms: number } | null;
  testing: boolean;
}

function friendlyTestError(raw: string | null | undefined): string {
  if (!raw) return "Connection failed.";
  const lower = raw.toLowerCase();
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

function isPromptVisible(p: CredentialPrompt, values: Record<string, string>): boolean {
  if (!p.when) return true;
  for (const [k, v] of Object.entries(p.when)) {
    if (values[k] !== v) return false;
  }
  return true;
}

const NEW_CRED = "__new__";

export default function EnterCredentials({
  catalog,
  authMethod,
  initialValues,
  initialBaseUrl,
  editing,
  selectedCredentialRef,
  onChange,
  onTest,
  testResult,
  testing,
}: Props) {
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [creds, setCreds] = useState<Credential[] | null>(null);

  const secretPrompts = useMemo(
    () => authMethod.prompts.filter((p) => p.secret),
    [authMethod.prompts],
  );
  const hasSecrets = secretPrompts.length > 0 || authMethod.prompts.some(
    (p) => p.name === "credential_name" || p.name === "credential_value",
  );

  const usingExisting = selectedCredentialRef !== null;

  useEffect(() => {
    if (!hasSecrets) return;
    listCredentials()
      .then(setCreds)
      .catch(() => setCreds([]));
  }, [hasSecrets]);

  const visiblePrompts = useMemo(
    () => authMethod.prompts.filter((p) => isPromptVisible(p, values)),
    [authMethod.prompts, values],
  );

  const nonSecretPrompts = useMemo(
    () =>
      usingExisting
        ? visiblePrompts.filter(
            (p) => !p.secret && p.name !== "credential_name" && p.name !== "credential_value",
          )
        : visiblePrompts,
    [visiblePrompts, usingExisting],
  );

  const fields = useMemo(
    () => nonSecretPrompts.map((p) => promptToFieldSchema(p, editing)),
    [nonSecretPrompts, editing],
  );

  const baseUrlInPrompts = visiblePrompts.some((p) => p.name === "base_url");

  function updateValues(next: Record<string, unknown>) {
    const stringified: Record<string, string> = {};
    for (const [k, v] of Object.entries(next)) stringified[k] = v == null ? "" : String(v);
    setValues(stringified);
    onChange(
      stringified,
      baseUrlInPrompts ? (stringified.base_url ?? "") : baseUrl,
      selectedCredentialRef,
    );
  }

  function updateBaseUrl(v: string) {
    setBaseUrl(v);
    onChange(values, v, selectedCredentialRef);
  }

  function handleCredentialSelect(name: string) {
    if (name === NEW_CRED) {
      const cleared = { ...values };
      delete cleared.credential_name;
      delete cleared.credential_value;
      for (const p of secretPrompts) delete cleared[p.name];
      setValues(cleared);
      onChange(
        cleared,
        baseUrlInPrompts ? (cleared.base_url ?? "") : baseUrl,
        null,
      );
    } else {
      onChange(values, baseUrlInPrompts ? (values.base_url ?? "") : baseUrl, name);
    }
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

      {hasSecrets && (
        <label className="provider-wizard-field">
          <span className="provider-wizard-field__label">Credential</span>
          <select
            className="form-input"
            value={selectedCredentialRef ?? NEW_CRED}
            onChange={(e) => handleCredentialSelect(e.target.value)}
          >
            <option value={NEW_CRED}>New credential…</option>
            {creds && creds.length > 0 && (
              <optgroup label="Saved credentials">
                {creds.map((c) => (
                  <option key={c.name} value={c.name}>
                    ${c.name} — {c.masked}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
      )}

      {!usingExisting && (
        <FormRenderer
          fields={fields}
          initialValues={values}
          onChange={updateValues}
          onSubmit={() => undefined}
          hideActions
        />
      )}

      {usingExisting && nonSecretPrompts.length > 0 && (
        <FormRenderer
          fields={fields}
          initialValues={values}
          onChange={updateValues}
          onSubmit={() => undefined}
          hideActions
        />
      )}

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
