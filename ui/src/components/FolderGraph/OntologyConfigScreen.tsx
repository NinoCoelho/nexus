/**
 * Full-pane editor for a per-folder graph's ontology (entity types + relations).
 *
 * Used in two states:
 *   - first-time: empty-ish lists, the wizard button surfaces the LLM proposer
 *   - edit-mode:  re-opens with the saved ontology pre-filled
 */

import { useState } from "react";
import type { FolderOntology } from "../../api/folderGraph";
import { OntologyWizard } from "./OntologyWizard";
import "./FolderGraph.css";

interface Props {
  folderPath: string;
  folderLabel: string;
  initial?: FolderOntology | null;
  /** Show the small "ontology drift — reindex required" banner. */
  pendingReindex?: boolean;
  onConfirm: (ontology: FolderOntology, opts: { triggerIndex: boolean }) => void | Promise<void>;
  onCancel: () => void;
  /** Used for the wizard SSE call. */
  onAutoPropose?: () => void;
}

const DEFAULT_ENTITY_TYPES = ["person", "organization", "concept", "document", "topic"];
const DEFAULT_RELATIONS = ["mentions", "about", "related_to", "part_of", "authored_by"];

export function OntologyConfigScreen({
  folderPath,
  folderLabel,
  initial,
  pendingReindex,
  onConfirm,
  onCancel,
}: Props) {
  const [entityTypes, setEntityTypes] = useState<string[]>(
    () => initial?.entity_types ?? DEFAULT_ENTITY_TYPES,
  );
  const [relations, setRelations] = useState<string[]>(
    () => initial?.relations ?? DEFAULT_RELATIONS,
  );
  const [allowCustom, setAllowCustom] = useState<boolean>(
    () => initial?.allow_custom_relations ?? true,
  );
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardActive, setWizardActive] = useState(false);
  const [saving, setSaving] = useState(false);

  function applyWizardOntology(o: FolderOntology) {
    setEntityTypes(o.entity_types ?? []);
    setRelations(o.relations ?? []);
    setAllowCustom(o.allow_custom_relations ?? true);
  }

  async function handleConfirm(triggerIndex: boolean) {
    const cleaned: FolderOntology = {
      entity_types: entityTypes.map((s) => s.trim()).filter(Boolean),
      relations: relations.map((s) => s.trim()).filter(Boolean),
      allow_custom_relations: allowCustom,
    };
    if (cleaned.entity_types.length === 0 || cleaned.relations.length === 0) {
      return;
    }
    setSaving(true);
    try {
      await onConfirm(cleaned, { triggerIndex });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fg-config-root">
      <div className="fg-config-header">
        <div className="fg-config-title">
          Configure ontology for <span className="fg-folder-name">{folderLabel}</span>
        </div>
        <div className="fg-config-subtitle">
          Pick the kinds of things and the kinds of links your folder is made of.
          You can edit these later.
        </div>
      </div>

      {pendingReindex && (
        <div className="fg-banner fg-banner--warn">
          You have unindexed changes — confirming will rebuild the graph.
        </div>
      )}

      <div className="fg-config-body">
        <div className="fg-config-actions-top">
          <button
            type="button"
            className="fg-btn fg-btn--secondary"
            onClick={() => setWizardOpen(true)}
            disabled={wizardActive}
          >
            ✨ Auto-propose with LLM
          </button>
        </div>

        {wizardOpen && (
          <OntologyWizard
            folderPath={folderPath}
            onAccept={(ontology) => {
              applyWizardOntology(ontology);
              setWizardOpen(false);
              setWizardActive(false);
            }}
            onCancel={() => {
              setWizardOpen(false);
              setWizardActive(false);
            }}
            onActiveChange={setWizardActive}
          />
        )}

        <div className="fg-section">
          <div className="fg-section-head">
            <span className="fg-section-title">Entity types</span>
            <span className="fg-section-hint">e.g. person, project, decision</span>
          </div>
          <ListEditor values={entityTypes} onChange={setEntityTypes} placeholder="entity_type" />
        </div>

        <div className="fg-section">
          <div className="fg-section-head">
            <span className="fg-section-title">Relations</span>
            <span className="fg-section-hint">e.g. mentions, depends_on, authored_by</span>
          </div>
          <ListEditor values={relations} onChange={setRelations} placeholder="relation_name" />
        </div>

        <div className="fg-section">
          <label className="fg-checkbox-row">
            <input
              type="checkbox"
              checked={allowCustom}
              onChange={(e) => setAllowCustom(e.target.checked)}
            />
            <span>
              Allow the LLM to add new relations beyond this list when extracting
            </span>
          </label>
        </div>
      </div>

      <div className="fg-config-footer">
        <button
          type="button"
          className="fg-btn fg-btn--ghost"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </button>
        <button
          type="button"
          className="fg-btn fg-btn--secondary"
          onClick={() => void handleConfirm(false)}
          disabled={saving}
        >
          Save without indexing
        </button>
        <button
          type="button"
          className="fg-btn fg-btn--primary"
          onClick={() => void handleConfirm(true)}
          disabled={saving}
        >
          Confirm &amp; build index
        </button>
      </div>
    </div>
  );
}

function ListEditor({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  return (
    <div className="fg-list-editor">
      {values.map((v, i) => (
        <div key={i} className="fg-list-row">
          <input
            className="fg-input"
            value={v}
            placeholder={placeholder}
            onChange={(e) => {
              const next = [...values];
              next[i] = e.target.value;
              onChange(next);
            }}
          />
          <button
            type="button"
            className="fg-icon-btn"
            onClick={() => onChange(values.filter((_, j) => j !== i))}
            title="Remove"
            aria-label={`Remove ${v}`}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="fg-btn fg-btn--ghost fg-btn--small"
        onClick={() => onChange([...values, ""])}
      >
        + Add
      </button>
    </div>
  );
}
