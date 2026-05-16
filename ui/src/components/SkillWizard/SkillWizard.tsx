/**
 * SkillWizard — capability wizard for non-technical users.
 *
 * Steps:
 *
 *   1. Ask       — textarea for a plain-language capability request.
 *   2. Searching — spinner while POST /skills/wizard/discover runs.
 *   3. Choose    — candidate cards (title, summary, complexity, cost,
 *                  required keys). Picking one moves to Keys.
 *   4. Keys      — checklist of API keys the candidate needs. Each missing
 *                  key gets an "Add key" button that opens a Modal (kind
 *                  prompt, secret). Once every key is set, "Continue"
 *                  moves to Build.
 *   5. Build     — POST /skills/wizard/build, subscribe to the returned
 *                  session's SSE stream, render friendly progress, end on
 *                  success / failure with a "Try it now" / "Done" CTA.
 *
 * Styling mirrors the existing ProviderWizard: full-screen overlay,
 * centered panel with header / body / footer rows.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  buildSkill,
  discoverSkills,
  listCredentials,
  setCredential,
  type Credential,
  type SkillCandidate,
  type SkillCandidateKey,
} from "../../api";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import { trackBackgroundBuild } from "../../hooks/useBackgroundSkillBuilds";
import {
  subscribeBuildStream,
  type BuildStreamHandle,
  type BuildStreamState,
} from "./buildStream";
import "./SkillWizard.css";

type Step = "ask" | "searching" | "choose" | "keys" | "plan" | "build";

type BuildState =
  | { kind: "success"; skillName: string }
  | { kind: "failure"; reason: string };

interface Props {
  onClose: () => void;
  /** Optional callback fired after a successful build so the caller can
   *  refresh dependent UI (e.g. the agent graph). */
  onSkillBuilt?: (skillName: string) => void;
}

export default function SkillWizard({ onClose, onSkillBuilt }: Props) {
  const { t, i18n } = useTranslation("skillWizard");
  const toast = useToast();
  const [step, setStep] = useState<Step>("ask");
  const [userAsk, setUserAsk] = useState("");
  const [candidates, setCandidates] = useState<SkillCandidate[]>([]);
  const [picked, setPicked] = useState<SkillCandidate | null>(null);
  // Plan-step state — populated when the user reaches the Plan screen.
  // ``planRefinement`` is appended to ``userAsk`` at build time so the
  // synth model receives the user's extra context. ``bundledIds`` is the
  // selected subset of related candidates (defaults to none — explicit
  // opt-in, not opt-out) and replaces the implicit "all the rest" list
  // we used to send to BuildStep.
  const [planRefinement, setPlanRefinement] = useState("");
  const [bundledIds, setBundledIds] = useState<string[]>([]);
  // Build session id, captured once the BuildStep POSTs /skills/wizard/build.
  // Used to hand off to the background tracker if the user dismisses the
  // wizard before the build finishes — closing the modal disconnects the
  // wizard's SSE but the server keeps running the turn.
  const [buildSessionId, setBuildSessionId] = useState<string | null>(null);
  const [buildTerminal, setBuildTerminal] = useState(false);

  const handleCloseWithBackgroundHandoff = useCallback(() => {
    // If we're still mid-build, register with the background tracker so a
    // toast fires when the server-side turn finishes. Skip when the build
    // already terminated (success/failure already shown) or never started.
    if (step === "build" && buildSessionId && !buildTerminal && picked) {
      trackBackgroundBuild({
        sessionId: buildSessionId,
        candidateTitle: picked.title,
        startedAt: Date.now(),
      });
      toast.info(t("bg.trackingStarted"), {
        detail: t("bg.trackingStartedDetail", { title: picked.title }),
      });
    }
    onClose();
  }, [
    step,
    buildSessionId,
    buildTerminal,
    picked,
    onClose,
    toast,
    t,
  ]);

  // Esc closes (the wizard is always dismissible — agent-side flow has no
  // first-run lock-in). Closing during a live build hands off to the
  // background tracker rather than dropping the build silently.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleCloseWithBackgroundHandoff();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [handleCloseWithBackgroundHandoff]);

  const handleSearch = useCallback(async () => {
    const ask = userAsk.trim();
    if (ask.length < 2) return;
    setStep("searching");
    setCandidates([]);
    try {
      const results = await discoverSkills(ask, i18n.language);
      setCandidates(results);
      setStep("choose");
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      toast.error(t("errors.loadFailed", { detail }));
      setStep("ask");
    }
  }, [userAsk, i18n.language, toast, t]);

  const handleBack = useCallback(() => {
    if (step === "choose") {
      setStep("ask");
      setCandidates([]);
      setPicked(null);
    } else if (step === "keys") {
      setStep("choose");
      setPicked(null);
    } else if (step === "plan") {
      // Plan → back to Keys if the candidate needs them, else Choose.
      // Preserve the picked candidate so the user doesn't have to
      // re-select if they only wanted to tweak refinement notes.
      if (picked && picked.requires_keys.length > 0) setStep("keys");
      else setStep("choose");
    }
  }, [step, picked]);

  const handlePick = useCallback((cand: SkillCandidate) => {
    setPicked(cand);
    // Reset any prior plan refinement so a different pick starts clean.
    setPlanRefinement("");
    setBundledIds([]);
    // Keys step only shows up when the candidate actually needs keys —
    // otherwise we go straight to Plan. Plan now sits between key setup
    // (or pick) and build so the user can refine scope + bundle related
    // skills before committing.
    setStep(cand.requires_keys.length === 0 ? "plan" : "keys");
  }, []);

  const handleProceedToPlan = useCallback(() => {
    setStep("plan");
  }, []);

  const handleProceedToBuild = useCallback(() => {
    setStep("build");
  }, []);

  const handleToggleBundle = useCallback((id: string) => {
    setBundledIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  // The userAsk that ultimately goes to /skills/wizard/build — original
  // ask plus any refinement the user typed on the plan screen.
  const augmentedUserAsk = useMemo(() => {
    const refine = planRefinement.trim();
    if (!refine) return userAsk;
    return `${userAsk}\n\nAdditional scope from refinement:\n${refine}`;
  }, [userAsk, planRefinement]);

  // Step counter shown in the header. Searching is a sub-state of "ask",
  // so we collapse it visually as step 1; keys is 3, plan is 4, build is
  // 5. When the picked candidate doesn't need keys we skip the Keys step
  // entirely — the counter follows the visible flow rather than the
  // hypothetical max so users don't see "Step 4 of 5" then jump to "5 of
  // 5" without a 4 in sight.
  const skipsKeys = !!picked && picked.requires_keys.length === 0;
  const totalSteps = skipsKeys ? 4 : 5;
  const stepIndex = (() => {
    if (step === "ask" || step === "searching") return 1;
    if (step === "choose") return 2;
    if (step === "keys") return 3;
    if (step === "plan") return skipsKeys ? 3 : 4;
    return skipsKeys ? 4 : 5; // build
  })();

  return (
    <div
      className="skill-wizard-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleCloseWithBackgroundHandoff();
      }}
    >
      <div className="skill-wizard-panel" role="dialog" aria-modal="true">
        <div className="skill-wizard-header">
          <span className="skill-wizard-title">{t("title")}</span>
          <span className="skill-wizard-step-counter">
            Step {stepIndex} of {totalSteps}
          </span>
          <button
            type="button"
            className="skill-wizard-close"
            onClick={handleCloseWithBackgroundHandoff}
            aria-label={t("close")}
          >
            ✕
          </button>
        </div>

        <div className="skill-wizard-body">
          {step === "ask" && (
            <AskStep
              value={userAsk}
              onChange={setUserAsk}
              onSubmit={handleSearch}
            />
          )}
          {step === "searching" && <SearchingStep />}
          {step === "choose" && (
            <ChooseStep
              candidates={candidates}
              onPick={handlePick}
              onTryAgain={handleBack}
            />
          )}
          {step === "keys" && picked && (
            <KeysStep
              candidate={picked}
              onContinue={handleProceedToPlan}
            />
          )}
          {step === "plan" && picked && (
            <PlanStep
              candidate={picked}
              related={candidates.filter((c) => c.id !== picked.id)}
              refinement={planRefinement}
              onChangeRefinement={setPlanRefinement}
              bundledIds={bundledIds}
              onToggleBundle={handleToggleBundle}
              onPickAnother={handleBack}
              onConfirm={handleProceedToBuild}
            />
          )}
          {step === "build" && picked && (
            <BuildStep
              candidate={picked}
              userAsk={augmentedUserAsk}
              relatedIds={bundledIds}
              language={i18n.language}
              onClose={handleCloseWithBackgroundHandoff}
              onSkillBuilt={(name) => onSkillBuilt?.(name)}
              onSessionStarted={setBuildSessionId}
              onTerminal={() => setBuildTerminal(true)}
            />
          )}
        </div>

        <div className="skill-wizard-footer">
          {(step === "choose" || step === "keys" || step === "plan") && (
            <button
              type="button"
              className="skill-wizard-secondary-btn"
              onClick={handleBack}
            >
              {t("back")}
            </button>
          )}
          <span style={{ flex: 1 }} />
          {step === "ask" && (
            <button
              type="button"
              className="skill-wizard-primary-btn"
              onClick={() => void handleSearch()}
              disabled={userAsk.trim().length < 2}
            >
              {t("ask.submit")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── steps ──────────────────────────────────────────────────────────────────

function AskStep({
  value,
  onChange,
  onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation("skillWizard");
  return (
    <div className="skill-wizard-step">
      <h2 className="skill-wizard-step-heading">{t("step.ask")}</h2>
      <p className="skill-wizard-helper">{t("ask.helper")}</p>
      <textarea
        className="skill-wizard-textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t("ask.placeholder")}
        rows={4}
        autoFocus
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
    </div>
  );
}

function SearchingStep() {
  const { t } = useTranslation("skillWizard");
  return (
    <div className="skill-wizard-step skill-wizard-searching">
      <div className="skill-wizard-spinner" aria-hidden="true" />
      <p className="skill-wizard-searching-line">{t("searching.registry")}</p>
      <p className="skill-wizard-searching-line skill-wizard-searching-secondary">
        {t("searching.analyzing")}
      </p>
    </div>
  );
}

function ChooseStep({
  candidates,
  onPick,
  onTryAgain,
}: {
  candidates: SkillCandidate[];
  onPick: (c: SkillCandidate) => void;
  onTryAgain: () => void;
}) {
  const { t } = useTranslation("skillWizard");

  if (candidates.length === 0) {
    return (
      <div className="skill-wizard-step skill-wizard-empty">
        <p>{t("choose.noResults")}</p>
        <button
          type="button"
          className="skill-wizard-primary-btn"
          onClick={onTryAgain}
        >
          {t("choose.tryAgain")}
        </button>
      </div>
    );
  }

  return (
    <div className="skill-wizard-step">
      <h2 className="skill-wizard-step-heading">{t("step.choose")}</h2>
      <ul className="skill-wizard-candidate-list">
        {candidates.map((c) => (
          <CandidateCard key={c.id} candidate={c} onPick={() => onPick(c)} />
        ))}
      </ul>
    </div>
  );
}

function CandidateCard({
  candidate,
  onPick,
}: {
  candidate: SkillCandidate;
  onPick: () => void;
}) {
  const { t } = useTranslation("skillWizard");
  const dots = "●".repeat(candidate.complexity) + "○".repeat(5 - candidate.complexity);
  const costLabel = t(`cost.${candidate.cost_tier}`);
  return (
    <li className="skill-wizard-candidate">
      <div className="skill-wizard-candidate-head">
        <h3 className="skill-wizard-candidate-title">{candidate.title}</h3>
        <span
          className={
            "skill-wizard-source-pill "
            + (candidate.source.verified
              ? "skill-wizard-source-pill-verified"
              : "skill-wizard-source-pill-unverified")
          }
          title={candidate.source.url}
        >
          {candidate.source.verified
            ? t("choose.verifiedSource")
            : t("choose.unverifiedSource")}
        </span>
      </div>
      <p className="skill-wizard-candidate-summary">{candidate.summary}</p>
      <div className="skill-wizard-candidate-meta">
        <span className="skill-wizard-meta-item">
          <span className="skill-wizard-meta-label">
            {t("choose.complexityLabel")}
          </span>
          <span className="skill-wizard-meta-dots" aria-hidden="true">
            {dots}
          </span>
          <span className="skill-wizard-meta-value">{candidate.complexity}/5</span>
        </span>
        <span className="skill-wizard-meta-item">
          <span className="skill-wizard-meta-label">{t("choose.costLabel")}</span>
          <span className="skill-wizard-meta-value">{costLabel}</span>
        </span>
      </div>
      <div className="skill-wizard-keys">
        <span className="skill-wizard-meta-label">{t("choose.keysLabel")}</span>
        {candidate.requires_keys.length === 0 ? (
          <span className="skill-wizard-meta-value">{t("choose.noKeys")}</span>
        ) : (
          <span className="skill-wizard-keys-chips">
            {candidate.requires_keys.map((k) => (
              <span key={k.name} className="skill-wizard-key-chip">
                {k.vendor || k.name}
                {k.free_tier_available ? " ✓" : ""}
              </span>
            ))}
          </span>
        )}
      </div>
      <div className="skill-wizard-candidate-actions">
        <button
          type="button"
          className="skill-wizard-primary-btn"
          onClick={onPick}
        >
          {t("choose.useThis")}
        </button>
      </div>
    </li>
  );
}

// ── keys step ─────────────────────────────────────────────────────────────

function KeysStep({
  candidate,
  onContinue,
}: {
  candidate: SkillCandidate;
  onContinue: () => void;
}) {
  const { t } = useTranslation("skillWizard");
  const toast = useToast();
  const [credentials, setCredentials] = useState<Credential[] | null>(null);
  const [editingKey, setEditingKey] = useState<SkillCandidateKey | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await listCredentials();
      setCredentials(list);
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      toast.error(t("errors.credsLoadFailed", { detail }));
      setCredentials([]);
    }
  }, [t, toast]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const presentKeys = useMemo(() => {
    if (credentials === null) return new Set<string>();
    return new Set(credentials.map((c) => c.name));
  }, [credentials]);

  const allReady =
    candidate.requires_keys.length === 0
      || candidate.requires_keys.every((k) => presentKeys.has(k.name));

  const handleSaveKey = useCallback(
    async (name: string, value: string) => {
      const trimmed = value.trim();
      if (!trimmed) {
        toast.error(t("keys.modal.valueEmpty"));
        return;
      }
      try {
        await setCredential(name, trimmed, { kind: "skill" });
        toast.success(t("keys.toast.saved", { name }));
        setEditingKey(null);
        await refresh();
      } catch (e) {
        toast.error(t("keys.toast.saveFailed", { name }), {
          detail: e instanceof Error ? e.message : undefined,
        });
      }
    },
    [refresh, t, toast],
  );

  if (candidate.requires_keys.length === 0) {
    return (
      <div className="skill-wizard-step">
        <h2 className="skill-wizard-step-heading">{t("keys.title")}</h2>
        <p className="skill-wizard-helper">{t("keys.noneNeeded")}</p>
        <div className="skill-wizard-candidate-actions">
          <button
            type="button"
            className="skill-wizard-primary-btn"
            onClick={onContinue}
          >
            {t("keys.continue")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="skill-wizard-step">
      <h2 className="skill-wizard-step-heading">{t("keys.title")}</h2>
      <p className="skill-wizard-helper">
        {t("keys.intro", { title: candidate.title })}
      </p>
      <ul className="skill-wizard-keys-list">
        {candidate.requires_keys.map((k) => {
          const present = presentKeys.has(k.name);
          return (
            <li
              key={k.name}
              className={
                "skill-wizard-key-row "
                + (present ? "skill-wizard-key-row-ready" : "skill-wizard-key-row-missing")
              }
            >
              <div className="skill-wizard-key-row-head">
                <code className="skill-wizard-key-name">{k.name}</code>
                {k.vendor && (
                  <span className="skill-wizard-key-vendor">{k.vendor}</span>
                )}
                <span className="skill-wizard-key-status">
                  {present ? "✓ " + t("keys.ready") : t("keys.missing")}
                </span>
              </div>
              <div className="skill-wizard-key-row-actions">
                {k.free_tier_available && (
                  <span className="skill-wizard-key-free-tier">
                    {t("keys.freeTier")}
                  </span>
                )}
                {k.get_key_url && (
                  <a
                    href={k.get_key_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="skill-wizard-key-link"
                  >
                    {t("keys.getKeyLink")}
                  </a>
                )}
                <button
                  type="button"
                  className="skill-wizard-secondary-btn"
                  onClick={() => setEditingKey(k)}
                >
                  {present ? t("keys.updateButton") : t("keys.addButton")}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <div className="skill-wizard-candidate-actions">
        <button
          type="button"
          className="skill-wizard-primary-btn"
          onClick={onContinue}
          disabled={!allReady}
        >
          {t("keys.continue")}
        </button>
      </div>

      {editingKey && (
        <Modal
          kind="prompt"
          secret
          title={t("keys.modal.title", { name: editingKey.name })}
          message={
            editingKey.vendor
              ? t("keys.modal.message", { vendor: editingKey.vendor })
              : t("keys.modal.messageNoVendor")
          }
          placeholder={t("keys.modal.placeholder")}
          confirmLabel={t("keys.modal.save")}
          onCancel={() => setEditingKey(null)}
          onSubmit={(v) => void handleSaveKey(editingKey.name, v)}
        />
      )}
    </div>
  );
}

// ── plan step ──────────────────────────────────────────────────────────────

interface PlanStepProps {
  candidate: SkillCandidate;
  related: SkillCandidate[];
  refinement: string;
  onChangeRefinement: (v: string) => void;
  bundledIds: string[];
  onToggleBundle: (id: string) => void;
  /** Go back to the picker so the user can swap the primary skill. */
  onPickAnother: () => void;
  onConfirm: () => void;
}

function PlanStep({
  candidate,
  related,
  refinement,
  onChangeRefinement,
  bundledIds,
  onToggleBundle,
  onPickAnother,
  onConfirm,
}: PlanStepProps) {
  const { t } = useTranslation("skillWizard");
  // Show at most six related candidates as togglable chips. Beyond that
  // the chip strip becomes a wall of noise and the user is better off
  // running another search. If they want a less-popular bundle they can
  // go back to the picker — that's the explicit "pick another" path.
  const bundleOptions = related.slice(0, 6);
  return (
    <div className="skill-wizard-step skill-wizard-plan">
      <h2 className="skill-wizard-step-heading">{t("plan.title")}</h2>
      <p className="skill-wizard-helper">{t("plan.helper")}</p>

      <div className="skill-wizard-plan-card">
        <h3 className="skill-wizard-candidate-title">{candidate.title}</h3>
        <p className="skill-wizard-candidate-summary">{candidate.summary}</p>
        {candidate.capabilities.length > 0 && (
          <div className="skill-wizard-plan-caps">
            {candidate.capabilities.map((cap) => (
              <span key={cap} className="skill-wizard-key-chip">
                {cap}
              </span>
            ))}
          </div>
        )}
      </div>

      <label className="skill-wizard-plan-label" htmlFor="plan-refinement">
        {t("plan.refineLabel")}
      </label>
      <p className="skill-wizard-helper skill-wizard-plan-sublabel">
        {t("plan.refineHelper")}
      </p>
      <textarea
        id="plan-refinement"
        className="skill-wizard-textarea"
        value={refinement}
        onChange={(e) => onChangeRefinement(e.target.value)}
        placeholder={t("plan.refinePlaceholder")}
        rows={4}
      />

      {bundleOptions.length > 0 && (
        <>
          <label className="skill-wizard-plan-label">
            {t("plan.bundleLabel")}
          </label>
          <p className="skill-wizard-helper skill-wizard-plan-sublabel">
            {t("plan.bundleHelper")}
          </p>
          <ul className="skill-wizard-plan-bundle">
            {bundleOptions.map((c) => {
              const checked = bundledIds.includes(c.id);
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    className={
                      "skill-wizard-bundle-chip"
                      + (checked ? " skill-wizard-bundle-chip--on" : "")
                    }
                    onClick={() => onToggleBundle(c.id)}
                    aria-pressed={checked}
                    title={c.summary}
                  >
                    <span className="skill-wizard-bundle-chip-mark" aria-hidden="true">
                      {checked ? "✓" : "+"}
                    </span>
                    <span className="skill-wizard-bundle-chip-title">{c.title}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <div className="skill-wizard-plan-actions">
        <button
          type="button"
          className="skill-wizard-secondary-btn"
          onClick={onPickAnother}
        >
          {t("plan.pickAnother")}
        </button>
        <button
          type="button"
          className="skill-wizard-primary-btn"
          onClick={onConfirm}
        >
          {t("plan.confirm")}
        </button>
      </div>
    </div>
  );
}


// ── build step ─────────────────────────────────────────────────────────────

interface BuildStepProps {
  candidate: SkillCandidate;
  userAsk: string;
  relatedIds: string[];
  language: string;
  onClose: () => void;
  onSkillBuilt?: (skillName: string) => void;
  /** Reports the build's session id back up to the wizard root so closing
   *  the modal mid-build can hand it off to the background tracker. */
  onSessionStarted: (sessionId: string) => void;
  /** Fired on EITHER success or failure — wizard root uses it to clear the
   *  background-handoff flag (no point in tracking a build that already
   *  resolved). */
  onTerminal: () => void;
}

function BuildStep({
  candidate,
  userAsk,
  relatedIds,
  language,
  onClose,
  onSkillBuilt,
  onSessionStarted,
  onTerminal,
}: BuildStepProps) {
  const { t } = useTranslation("skillWizard");
  const toast = useToast();

  const [stream, setStream] = useState<BuildStreamState>({
    stage: "starting",
    subagentsTotal: 0,
    subagentsCompleted: 0,
    iterations: 0,
  });
  const [terminal, setTerminal] = useState<BuildState | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const startedAtRef = useRef<number>(Date.now());
  const launchedRef = useRef(false);
  const handleRef = useRef<BuildStreamHandle | null>(null);

  // Live elapsed clock — refresh once a second while the build is in flight.
  useEffect(() => {
    if (terminal) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [terminal]);

  useEffect(() => {
    if (launchedRef.current) return;
    launchedRef.current = true;

    let cancelled = false;
    const start = async () => {
      try {
        const res = await buildSkill({
          candidateId: candidate.id,
          userAsk,
          relatedIds,
          language,
        });
        if (cancelled) return;
        onSessionStarted(res.session_id);
        handleRef.current = subscribeBuildStream(res.session_id, {
          onState: (next) => setStream(next),
          onTerminal: (outcome) => {
            if (outcome.kind === "success") {
              setTerminal({ kind: "success", skillName: outcome.skillName });
              onSkillBuilt?.(outcome.skillName);
            } else {
              setTerminal({ kind: "failure", reason: outcome.reason });
            }
            onTerminal();
          },
        });
      } catch (e) {
        const detail = e instanceof Error ? e.message : String(e);
        toast.error(t("errors.buildFailed", { detail }));
        setTerminal({ kind: "failure", reason: detail });
        onTerminal();
      }
    };

    void start();

    return () => {
      cancelled = true;
      handleRef.current?.close();
    };
  }, [
    candidate.id,
    userAsk,
    relatedIds,
    language,
    onSkillBuilt,
    onSessionStarted,
    onTerminal,
    toast,
    t,
  ]);

  if (terminal?.kind === "success") {
    return (
      <div className="skill-wizard-step skill-wizard-build-success">
        <div className="skill-wizard-build-icon" aria-hidden="true">✓</div>
        <h2 className="skill-wizard-step-heading">{t("build.success")}</h2>
        <p className="skill-wizard-helper">
          {t("build.successDetail")}
          <br />
          <code>{terminal.skillName}</code>
        </p>
        <div className="skill-wizard-candidate-actions">
          <button
            type="button"
            className="skill-wizard-primary-btn"
            onClick={onClose}
          >
            {t("build.doneButton")}
          </button>
        </div>
      </div>
    );
  }

  if (terminal?.kind === "failure") {
    return (
      <div className="skill-wizard-step skill-wizard-build-failure">
        <div className="skill-wizard-build-icon" aria-hidden="true">✕</div>
        <h2 className="skill-wizard-step-heading">{t("build.failed")}</h2>
        <p className="skill-wizard-helper">
          {t("build.failedDetail")}
          {terminal.reason && (
            <>
              <br />
              <code>{terminal.reason}</code>
            </>
          )}
        </p>
        <div className="skill-wizard-candidate-actions">
          <button
            type="button"
            className="skill-wizard-secondary-btn"
            onClick={onClose}
          >
            {t("build.doneButton")}
          </button>
        </div>
      </div>
    );
  }

  // In-progress
  const elapsedSeconds = Math.floor((now - startedAtRef.current) / 1000);
  const elapsed = formatElapsed(elapsedSeconds);

  let activityLine = t("build.stages.starting");
  if (stream.stage === "reviewing") {
    activityLine =
      stream.subagentsTotal > 0
        ? t("build.stages.reviewingCount", { count: stream.subagentsTotal })
        : t("build.stages.reviewing");
  } else if (stream.stage === "synthesizing") {
    activityLine = t("build.stages.synthesizing");
  } else if (stream.stage === "saving") {
    activityLine = t("build.stages.saving");
  }

  // Reveal the "Run in background" affordance after the first 4 seconds —
  // the build often takes 30-90 s on cold cache; offering immediately would
  // tempt users into clicking it before realizing it's worth waiting at all.
  const showBackground = elapsedSeconds >= 4;

  return (
    <div className="skill-wizard-step skill-wizard-searching">
      <div className="skill-wizard-spinner" aria-hidden="true" />
      <p className="skill-wizard-searching-line">{t("build.title")}</p>
      <p className="skill-wizard-helper">{t("build.intro")}</p>
      <p className="skill-wizard-searching-line skill-wizard-searching-secondary">
        {activityLine}
      </p>
      <p className="skill-wizard-build-meta">
        <span>{t("build.elapsed", { elapsed })}</span>
        {stream.iterations > 0 && (
          <>
            <span aria-hidden="true"> · </span>
            <span>{t("build.iterations", { count: stream.iterations })}</span>
          </>
        )}
      </p>
      {showBackground && (
        <button
          type="button"
          className="skill-wizard-secondary-btn skill-wizard-build-bg-btn"
          onClick={onClose}
        >
          {t("build.runInBackground")}
        </button>
      )}
    </div>
  );
}

function formatElapsed(totalSeconds: number): string {
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}
