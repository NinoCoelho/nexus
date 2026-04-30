import type { AuthMethod, ProviderCatalogEntry } from "../../../api";
import { SUPPORTED_AUTH_METHODS } from "../types";

interface Props {
  catalog: ProviderCatalogEntry;
  onPick: (method: AuthMethod) => void;
  onUnsupported: (method: AuthMethod) => void;
}

const METHOD_BLURB: Record<string, string> = {
  api: "Use a static API key from the provider's dashboard.",
  oauth_device: "Sign in with your browser; works for Pro / Max subscriptions.",
  oauth_redirect: "Sign in via redirect; the provider opens a callback page.",
  iam_aws: "Authenticate with an AWS profile or IAM role.",
  iam_gcp: "Authenticate with a Google Cloud service account.",
  iam_azure: "Authenticate with an Azure resource + key.",
  anonymous: "No auth — local server.",
};

export default function SelectAuthMethod({ catalog, onPick, onUnsupported }: Props) {
  const methods = [...catalog.auth_methods].sort((a, b) => a.priority - b.priority);
  return (
    <div className="provider-wizard-step provider-wizard-step--auth">
      <h3 className="provider-wizard-step__title">
        How do you want to connect to {catalog.display_name}?
      </h3>
      <div className="provider-wizard-auth-list">
        {methods.map((m) => {
          const supported = SUPPORTED_AUTH_METHODS.includes(m.id);
          return (
            <button
              key={m.id}
              type="button"
              className={`provider-wizard-auth-card${supported ? "" : " provider-wizard-auth-card--soon"}`}
              onClick={() => (supported ? onPick(m) : onUnsupported(m))}
            >
              <span className="provider-wizard-auth-card__title">{m.label}</span>
              <span className="provider-wizard-auth-card__blurb">
                {METHOD_BLURB[m.id] ?? ""}
              </span>
              {!supported && (
                <span className="provider-wizard-auth-card__badge">Coming soon</span>
              )}
              {m.requires_extra && (
                <span className="provider-wizard-auth-card__hint">
                  Requires nexus[{m.requires_extra}] install.
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
