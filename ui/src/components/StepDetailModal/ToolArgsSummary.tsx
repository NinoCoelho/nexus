// StepDetailModal — humanized prose summary of tool arguments.

import React from "react";

interface Props { tool?: string; args: unknown; }

export default function ToolArgsSummary({ tool, args }: Props) {
  if (!tool || !args || typeof args !== "object") return null;
  const a = args as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" ? v : "");

  let content: React.ReactNode = null;

  switch (tool) {
    case "vault_read":
      content = <>Reading <code className="sdm-arg-code">{str(a.path)}</code></>;
      break;
    case "vault_write": {
      const chars = typeof a.content === "string" ? a.content.length : null;
      content = (
        <>
          Writing to <code className="sdm-arg-code">{str(a.path)}</code>
          {chars != null && <span className="sdm-arg-dim"> · {chars.toLocaleString()} chars</span>}
        </>
      );
      break;
    }
    case "vault_list":
      content = <>Listing <code className="sdm-arg-code">{str(a.path) || "/"}</code></>;
      break;
    case "vault_search":
    case "vault_semantic_search":
      content = <>Searching for <span className="sdm-arg-query">"{str(a.query)}"</span></>;
      break;
    case "vault_tags":
      content = a.path
        ? <>Tags on <code className="sdm-arg-code">{str(a.path)}</code></>
        : <>Listing all tags</>;
      break;
    case "vault_backlinks":
      content = <>Backlinks for <code className="sdm-arg-code">{str(a.path)}</code></>;
      break;
    case "http_call": {
      const method = str(a.method) || "GET";
      const url = str(a.url);
      content = (
        <>
          <span className="sdm-arg-method">{method}</span>{" "}
          <code className="sdm-arg-code sdm-arg-url">{url.length > 80 ? url.slice(0, 80) + "…" : url}</code>
        </>
      );
      break;
    }
    case "terminal":
      content = <code className="sdm-arg-code sdm-arg-cmd">{str(a.command)}</code>;
      break;
    case "skill_manage": {
      const action = str(a.action);
      const name = str(a.name);
      content = (
        <>
          {action || "manage"} skill{name ? <> <strong>{name}</strong></> : null}
        </>
      );
      break;
    }
    case "skill_view":
      content = <>Reading skill <strong>{str(a.name)}</strong></>;
      break;
    case "skills_list":
      content = <>Listing skills</>;
      break;
    case "kanban_manage": {
      const action = str(a.action);
      const board = str(a.board ?? a.path ?? "");
      content = (
        <>
          {action || "manage"}{board ? <> on <code className="sdm-arg-code">{board}</code></> : null}
        </>
      );
      break;
    }
    default: {
      const entries = Object.entries(a).filter(([, v]) => v != null);
      if (entries.length === 0) return null;
      content = (
        <>
          {entries.map(([k, v], i) => (
            <span key={k}>
              {i > 0 && <span className="sdm-arg-dim"> · </span>}
              <span className="sdm-arg-dim">{k}:</span>{" "}
              <code className="sdm-arg-code">
                {typeof v === "string"
                  ? v.length > 80 ? v.slice(0, 80) + "…" : v
                  : JSON.stringify(v)}
              </code>
            </span>
          ))}
        </>
      );
    }
  }

  if (!content) return null;
  return <p className="sdm-args-prose">{content}</p>;
}
