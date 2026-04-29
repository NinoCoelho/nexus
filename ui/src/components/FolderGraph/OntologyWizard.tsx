/**
 * Inline LLM-driven ontology proposer.
 *
 * Streams via SSE from /graph/folder/ontology-wizard/start, renders the
 * model's clarifying questions as cards, and pushes user answers through
 * /graph/folder/ontology-wizard/answer until the model emits `done`.
 */

import { useEffect, useRef, useState } from "react";
import {
  answerOntologyWizard,
  startOntologyWizard,
  type FolderOntology,
  type OntologyWizardEvent,
  type OntologyWizardQuestion,
} from "../../api/folderGraph";

interface Props {
  folderPath: string;
  onAccept: (ontology: FolderOntology) => void;
  onCancel: () => void;
  onActiveChange?: (active: boolean) => void;
}

export function OntologyWizard({ folderPath, onAccept, onCancel, onActiveChange }: Props) {
  const [wizardId, setWizardId] = useState<string | null>(null);
  const [proposal, setProposal] = useState<FolderOntology | null>(null);
  const [rationale, setRationale] = useState<string>("");
  const [question, setQuestion] = useState<OntologyWizardQuestion | null>(null);
  const [status, setStatus] = useState<string>("Starting wizard…");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<FolderOntology | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [freeText, setFreeText] = useState<string>("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    onActiveChange?.(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let cancelled = false;

    (async () => {
      try {
        await startOntologyWizard(
          folderPath,
          (e: OntologyWizardEvent) => {
            if (cancelled) return;
            if (e.type === "wizard_id") setWizardId(e.wizard_id);
            else if (e.type === "status") setStatus(e.message);
            else if (e.type === "proposal") {
              setProposal(e.ontology);
              setRationale(e.rationale);
              setStatus("");
            } else if (e.type === "question") {
              setQuestion(e.question);
              setStatus("");
            } else if (e.type === "done") {
              setDone(e.ontology);
              setProposal(e.ontology);
              setRationale(e.rationale);
              setQuestion(null);
              setStatus("");
            } else if (e.type === "error") {
              setError(e.detail);
              setStatus("");
            }
          },
          ctrl.signal,
        );
      } catch (err) {
        if (!cancelled && (err instanceof Error ? err.name : "") !== "AbortError") {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();

    return () => {
      cancelled = true;
      ctrl.abort();
      onActiveChange?.(false);
    };
  }, [folderPath, onActiveChange]);

  async function submitAnswer(answer: string) {
    if (!wizardId) return;
    setSubmitting(true);
    setQuestion(null);
    setStatus("Thinking about your answer…");
    try {
      await answerOntologyWizard(wizardId, answer);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
      setFreeText("");
    }
  }

  return (
    <div className="fg-wizard">
      <div className="fg-wizard-header">
        <span className="fg-wizard-title">✨ Ontology wizard</span>
        <button type="button" className="fg-icon-btn" onClick={onCancel} title="Cancel wizard">
          ×
        </button>
      </div>

      {error && (
        <div className="fg-banner fg-banner--error">
          {error}
        </div>
      )}

      {!error && rationale && (
        <div className="fg-wizard-rationale">{rationale}</div>
      )}

      {!error && proposal && !done && (
        <div className="fg-wizard-preview">
          <div className="fg-wizard-row">
            <span className="fg-wizard-label">Entity types</span>
            <div className="fg-chip-row">
              {proposal.entity_types.map((t) => (
                <span key={t} className="fg-chip fg-chip--provisional">{t}</span>
              ))}
            </div>
          </div>
          <div className="fg-wizard-row">
            <span className="fg-wizard-label">Relations</span>
            <div className="fg-chip-row">
              {proposal.relations.map((r) => (
                <span key={r} className="fg-chip fg-chip--provisional">{r}</span>
              ))}
            </div>
          </div>
        </div>
      )}

      {!error && question && (
        <div className="fg-wizard-question">
          <div className="fg-wizard-question-text">{question.text}</div>
          {question.choices.length > 0 ? (
            <div className="fg-wizard-choice-row">
              {question.choices.map((c) => (
                <button
                  key={c}
                  type="button"
                  className="fg-btn fg-btn--secondary"
                  disabled={submitting}
                  onClick={() => void submitAnswer(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          ) : null}
          <div className="fg-wizard-freetext">
            <input
              type="text"
              className="fg-input"
              placeholder="Or type your own answer…"
              value={freeText}
              disabled={submitting}
              onChange={(e) => setFreeText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && freeText.trim()) {
                  void submitAnswer(freeText.trim());
                }
              }}
            />
          </div>
        </div>
      )}

      {!error && status && !question && !done && (
        <div className="fg-wizard-status">{status}</div>
      )}

      {!error && done && (
        <div className="fg-wizard-actions">
          <span className="fg-wizard-status">Ready — accept to populate the editor</span>
          <button
            type="button"
            className="fg-btn fg-btn--primary"
            onClick={() => onAccept(done)}
          >
            Use this ontology
          </button>
        </div>
      )}
    </div>
  );
}
