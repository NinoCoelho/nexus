import { useEffect, useRef, useState } from "react";
import {
  claimClaudeCodeCredentials,
  claimCodexCredentials,
  type AuthMethod,
  type ProviderCatalogEntry,
} from "../../../api";

interface Props {
  catalog: ProviderCatalogEntry;
  authMethod: AuthMethod;
  onComplete: (credentialRef: string) => void;
  onCancel: () => void;
}

const SOURCE_LABEL: Record<string, string> = {
  local_claude_code: "Claude Code",
  local_codex: "Codex CLI",
};

/**
 * "Use Claude Code sign-in" / "Use Codex sign-in" — the wizard talks to
 * a local-only endpoint that reads the other tool's stored credential
 * and copies it into our secrets store. No OAuth round-trip; the user
 * already authed through that other tool.
 *
 * One round-trip on mount, then auto-advance on success.
 */
export default function ClaimLocalCreds({
  catalog: _catalog,
  authMethod,
  onComplete,
  onCancel,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [subtitle, setSubtitle] = useState<string | null>(null);
  const startedRef = useRef(false);
  const sourceLabel = SOURCE_LABEL[authMethod.id] ?? authMethod.label;

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    let cancelled = false;
    void (async () => {
      try {
        let credentialRef: string;
        if (authMethod.id === "local_claude_code") {
          const res = await claimClaudeCodeCredentials();
          if (cancelled) return;
          credentialRef = res.credential_ref;
          setSubtitle(
            res.subscription
              ? `Imported ${res.subscription} subscription.`
              : "Imported sign-in.",
          );
        } else if (authMethod.id === "local_codex") {
          const res = await claimCodexCredentials();
          if (cancelled) return;
          credentialRef = res.credential_ref;
          setSubtitle(`Imported ${res.auth_mode ?? "API"} key.`);
        } else {
          throw new Error(
            `Local-credential adoption for "${authMethod.id}" is not implemented yet.`,
          );
        }
        // Brief delay so the success line is visible before the wizard
        // jumps to step 4. Pure cosmetic — feels less abrupt than an
        // instant transition.
        setTimeout(() => {
          if (!cancelled) onComplete(credentialRef);
        }, 600);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authMethod.id, onComplete]);

  if (error) {
    return (
      <div className="provider-wizard-step provider-wizard-step--oauth">
        <h3 className="provider-wizard-step__title">Couldn't import {sourceLabel} sign-in</h3>
        <p className="provider-wizard-step__subtitle">{error}</p>
        <button
          type="button"
          className="provider-wizard-secondary-btn"
          onClick={onCancel}
        >
          Back
        </button>
      </div>
    );
  }

  return (
    <div className="provider-wizard-step provider-wizard-step--oauth">
      <h3 className="provider-wizard-step__title">
        Importing {sourceLabel} sign-in
      </h3>
      <p className="provider-wizard-step__subtitle">
        Reading the credentials {sourceLabel} already stored on this machine — no extra sign-in needed.
        {authMethod.id === "local_claude_code" && (
          <>
            {" "}Nexus will identify itself as Claude Code on Anthropic API calls so your Pro/Max
            rate-limit allocation applies. This crosses into ToS-gray territory — Anthropic permits
            the OAuth bundle through official products only.
          </>
        )}
      </p>
      <div className="provider-wizard-oauth-block">
        <p className="provider-wizard-oauth-status">
          {subtitle ? `✓ ${subtitle} Continuing…` : "Reading…"}
        </p>
      </div>
    </div>
  );
}
