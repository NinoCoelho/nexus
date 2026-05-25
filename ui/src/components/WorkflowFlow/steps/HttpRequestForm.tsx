import TemplateInput from "../TemplateInput";
import { AUTH_TYPES, API_KEY_LOCATIONS } from "./constants";
import type { StepFormProps } from "./shared";
import { useCredentials } from "../../../hooks/useCredentials";

export default function HttpRequestForm({
  step,
  onChangeStep,
  stepRefs,
  stepSchemas,
}: StepFormProps) {
  const { credentials } = useCredentials();

  return (
    <>
      <div className="wf-field-row">
        <div className="wf-field">
          <label>Method</label>
          <select
            value={step.method || "GET"}
            onChange={(e) => onChangeStep({ method: e.target.value })}
          >
            {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="wf-field">
        <label>URL</label>
        <TemplateInput
          value={step.url || ""}
          onChange={(val) => onChangeStep({ url: val })}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          placeholder="https://api.example.com/data"
        />
      </div>

      <div className="wf-section-label">Authentication</div>

      <div className="wf-field">
        <label>Type</label>
        <select
          value={step.auth_type || "none"}
          onChange={(e) => onChangeStep({ auth_type: e.target.value })}
        >
          {AUTH_TYPES.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      {step.auth_type === "apikey" && (
        <>
          <div className="wf-field">
            <label>Credential</label>
            <select
              value={step.auth_credential || ""}
              onChange={(e) =>
                onChangeStep({ auth_credential: e.target.value })
              }
            >
              <option value="">— select credential —</option>
              {credentials.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="wf-field">
            <label>Location</label>
            <select
              value={step.auth_location || "header"}
              onChange={(e) => onChangeStep({ auth_location: e.target.value })}
            >
              {API_KEY_LOCATIONS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          {(step.auth_location || "header") === "header" && (
            <>
              <div className="wf-field">
                <label>Header Name</label>
                <input
                  value={step.auth_header_name || ""}
                  onChange={(e) =>
                    onChangeStep({ auth_header_name: e.target.value })
                  }
                  placeholder="Authorization"
                />
              </div>
              <div className="wf-field">
                <label>Prefix</label>
                <input
                  value={step.auth_prefix || ""}
                  onChange={(e) =>
                    onChangeStep({ auth_prefix: e.target.value })
                  }
                  placeholder="Bearer"
                />
              </div>
            </>
          )}
          {(step.auth_location || "header") === "query" && (
            <div className="wf-field">
              <label>Query Param</label>
              <input
                value={step.auth_query_name || ""}
                onChange={(e) =>
                  onChangeStep({ auth_query_name: e.target.value })
                }
                placeholder="api_key"
              />
            </div>
          )}
        </>
      )}

      {step.auth_type === "basic" && (
        <>
          <div className="wf-field">
            <label>Username</label>
            <input
              value={step.auth_username || ""}
              onChange={(e) =>
                onChangeStep({ auth_username: e.target.value })
              }
              placeholder="user"
            />
          </div>
          <div className="wf-field">
            <label>Password Credential</label>
            <select
              value={step.auth_password_credential || ""}
              onChange={(e) =>
                onChangeStep({ auth_password_credential: e.target.value })
              }
            >
              <option value="">— select credential —</option>
              {credentials.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </>
      )}

      {step.auth_type === "oauth" && (
        <div className="wf-field">
          <label>Token Credential</label>
          <select
            value={step.auth_credential || ""}
            onChange={(e) =>
              onChangeStep({ auth_credential: e.target.value })
            }
          >
            <option value="">— select credential —</option>
            {credentials.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="wf-section-label">Custom Headers</div>
      <div className="wf-field">
        <label>Headers (JSON)</label>
        <TemplateInput
          value={
            step.custom_headers
              ? JSON.stringify(step.custom_headers, null, 2)
              : ""
          }
          onChange={(val) => {
            try {
              onChangeStep({ custom_headers: JSON.parse(val) });
            } catch {}
          }}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          multiline
          minLines={2}
          placeholder='{"X-Custom": "value"}'
        />
      </div>

      <div className="wf-section-label">Body</div>
      <div className="wf-field">
        <label>Body (JSON)</label>
        <TemplateInput
          value={step.body ? JSON.stringify(step.body, null, 2) : ""}
          onChange={(val) => {
            try {
              onChangeStep({ body: JSON.parse(val) });
            } catch {}
          }}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          multiline
          minLines={3}
          placeholder='{"key": "value"}'
        />
      </div>
    </>
  );
}
