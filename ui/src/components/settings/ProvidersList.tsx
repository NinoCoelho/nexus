/**
 * ProvidersList — flat per-provider list, replaces the old ProvidersSection.
 *
 * Each provider card shows status + model chips (each removable) + a
 * trailing `[ + ]` chip that opens a small popover with catalog defaults,
 * a "discover from API" button, and a free-form input.
 *
 * Provider header has [edit] (opens the wizard in edit mode) and [×]
 * (delete with confirm). A trailing "+ Add provider" button opens the
 * wizard in add mode.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  deleteModel,
  deleteProvider,
  fetchProviderCatalog,
  fetchProviderModels,
  postModel,
  type Model,
  type Provider,
  type ProviderCatalogEntry,
} from "../../api";
import { WizardModal } from "../ProviderWizard";
import type { WizardEditPrefill, WizardMode } from "../ProviderWizard";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import "./ProvidersList.css";

interface Props {
  providers: Provider[];
  models: Model[];
  onRefresh: () => void;
}

function statusClass(p: Provider) {
  if (p.key_source === "anonymous") return "providers-list-dot providers-list-dot--anon";
  return p.has_key ? "providers-list-dot providers-list-dot--ok" : "providers-list-dot providers-list-dot--bad";
}

function statusLabel(p: Provider): string {
  if (p.key_source === "anonymous") return "anonymous";
  if (p.has_key && p.key_source === "credential") return `via ${p.credential_ref}`;
  if (p.has_key && p.key_source === "env") return `via $${p.key_env}`;
  if (p.has_key && p.key_source === "inline") return "inline (legacy)";
  return "not configured";
}

interface AddModelPopoverProps {
  provider: Provider;
  catalogEntry: ProviderCatalogEntry | null;
  existing: string[];
  onAdd: (modelName: string) => Promise<void>;
  onClose: () => void;
}

function AddModelPopover({
  provider,
  catalogEntry,
  existing,
  onAdd,
  onClose,
}: AddModelPopoverProps) {
  const toast = useToast();
  const [discovered, setDiscovered] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [custom, setCustom] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [onClose]);

  async function discover() {
    setLoading(true);
    try {
      const res = await fetchProviderModels(provider.name);
      if (res.ok) setDiscovered(res.models);
      else
        toast.warning("Could not list models.", {
          detail: res.error ?? undefined,
        });
    } catch (e) {
      toast.error("Discovery failed.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }

  const palette = Array.from(
    new Set<string>([
      ...(catalogEntry?.default_models ?? []).map((m) => m.id),
      ...(discovered ?? []),
    ]),
  ).filter((m) => !existing.includes(m));

  async function handleAdd(name: string) {
    const m = (name ?? "").trim();
    if (!m) return;
    if (existing.includes(m)) {
      toast.info("Model already added.");
      return;
    }
    await onAdd(m);
    setCustom("");
  }

  return (
    <div ref={ref} className="providers-list-popover">
      <div className="providers-list-popover__title">Add a model</div>
      {palette.length > 0 && (
        <>
          <div className="providers-list-popover__section">From catalog{discovered ? " + provider" : ""}</div>
          <div className="providers-list-popover__chips">
            {palette.map((m) => (
              <button
                key={m}
                type="button"
                className="provider-wizard-chip"
                onClick={() => void handleAdd(m)}
              >
                {m}
              </button>
            ))}
          </div>
        </>
      )}
      <button
        type="button"
        className="provider-wizard-secondary-btn providers-list-popover__discover"
        onClick={() => void discover()}
        disabled={loading}
      >
        {loading ? "Discovering…" : "Discover from provider"}
      </button>
      <div className="providers-list-popover__custom">
        <input
          className="form-input"
          placeholder="Or type a model id…"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void handleAdd(custom);
            }
          }}
        />
        <button
          type="button"
          className="provider-wizard-secondary-btn"
          onClick={() => void handleAdd(custom)}
          disabled={!custom.trim()}
        >
          Add
        </button>
      </div>
    </div>
  );
}

export default function ProvidersList({ providers, models, onRefresh }: Props) {
  const toast = useToast();
  const [catalog, setCatalog] = useState<ProviderCatalogEntry[] | null>(null);
  const [wizardMode, setWizardMode] = useState<WizardMode | null>(null);
  const [editPrefill, setEditPrefill] = useState<WizardEditPrefill | undefined>(undefined);
  const [popoverFor, setPopoverFor] = useState<string | null>(null);
  const [confirmRemoveProvider, setConfirmRemoveProvider] = useState<string | null>(null);
  const [confirmRemoveModel, setConfirmRemoveModel] = useState<{ provider: string; modelId: string } | null>(null);

  useEffect(() => {
    fetchProviderCatalog()
      .then(setCatalog)
      .catch((e) =>
        toast.error("Could not load provider catalog.", {
          detail: e instanceof Error ? e.message : undefined,
        }),
      );
  }, [toast]);

  const modelsByProvider = useMemo(() => {
    const out: Record<string, Model[]> = {};
    for (const m of models) (out[m.provider] ??= []).push(m);
    return out;
  }, [models]);

  const catalogById = useMemo(() => {
    const out: Record<string, ProviderCatalogEntry> = {};
    for (const e of catalog ?? []) out[e.id] = e;
    return out;
  }, [catalog]);

  function openEdit(p: Provider) {
    const c = catalogById[p.name] ?? null;
    setEditPrefill({
      providerName: p.name,
      catalog: c,
      authMethod: null,
      baseUrl: p.base_url ?? "",
      models: (modelsByProvider[p.name] ?? []).map((m) => m.model_name),
      credentialRef: p.credential_ref ?? null,
    });
    setWizardMode("edit");
  }

  async function handleAddModel(provider: Provider, modelName: string) {
    try {
      await postModel({
        id: `${provider.name}/${modelName}`,
        provider: provider.name,
        model_name: modelName,
        tags: [],
        tier: "balanced",
        notes: "",
      });
      toast.success(`Added ${modelName}.`);
      onRefresh();
      setPopoverFor(null);
    } catch (e) {
      toast.error("Could not add model.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function handleRemoveModel(modelId: string) {
    try {
      await deleteModel(modelId);
      toast.success("Model removed.");
      onRefresh();
    } catch (e) {
      toast.error("Could not remove model.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setConfirmRemoveModel(null);
    }
  }

  async function handleRemoveProvider(name: string) {
    try {
      await deleteProvider(name);
      toast.success(`Removed ${name}.`);
      onRefresh();
    } catch (e) {
      toast.error("Could not remove provider.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setConfirmRemoveProvider(null);
    }
  }

  return (
    <div className="providers-list">
      {providers.length === 0 && (
        <p className="providers-list-empty">No providers configured yet.</p>
      )}

      {providers.map((p) => {
        const provModels = modelsByProvider[p.name] ?? [];
        return (
          <div key={p.name} className="providers-list-card">
            <div className="providers-list-card__header">
              <span className={statusClass(p)} aria-hidden="true" />
              <span className="providers-list-card__name">{p.name}</span>
              <span className="providers-list-card__status">{statusLabel(p)}</span>
              <span className="providers-list-card__spacer" />
              <button
                type="button"
                className="providers-list-card__action"
                onClick={() => openEdit(p)}
                aria-label={`Edit ${p.name}`}
              >
                Edit
              </button>
              <button
                type="button"
                className="providers-list-card__action providers-list-card__action--danger"
                onClick={() => setConfirmRemoveProvider(p.name)}
                aria-label={`Remove ${p.name}`}
              >
                ✕
              </button>
            </div>
            <div className="providers-list-card__body">
              {provModels.map((m) => (
                <span key={m.id} className="providers-list-chip">
                  <span>{m.model_name}</span>
                  <button
                    type="button"
                    className="providers-list-chip__x"
                    onClick={() =>
                      setConfirmRemoveModel({ provider: p.name, modelId: m.id })
                    }
                    aria-label={`Remove ${m.model_name}`}
                  >
                    ✕
                  </button>
                </span>
              ))}
              <div className="providers-list-add-wrap">
                <button
                  type="button"
                  className="providers-list-add-chip"
                  onClick={() => setPopoverFor(popoverFor === p.name ? null : p.name)}
                >
                  +
                </button>
                {popoverFor === p.name && (
                  <AddModelPopover
                    provider={p}
                    catalogEntry={catalogById[p.name] ?? null}
                    existing={provModels.map((m) => m.model_name)}
                    onAdd={(name) => handleAddModel(p, name)}
                    onClose={() => setPopoverFor(null)}
                  />
                )}
              </div>
            </div>
          </div>
        );
      })}

      <button
        type="button"
        className="providers-list-add-provider"
        onClick={() => {
          setEditPrefill(undefined);
          setWizardMode("add");
        }}
      >
        + Add provider
      </button>

      {wizardMode && (
        <WizardModal
          mode={wizardMode}
          configuredNames={providers.map((p) => p.name)}
          editPrefill={editPrefill}
          onClose={(result) => {
            setWizardMode(null);
            setEditPrefill(undefined);
            if (result.saved) onRefresh();
          }}
        />
      )}

      {confirmRemoveProvider && (
        <Modal
          kind="confirm"
          title={`Remove ${confirmRemoveProvider}?`}
          message="This removes the provider and any models registered against it. Stored credentials are kept."
          confirmLabel="Remove"
          danger
          onCancel={() => setConfirmRemoveProvider(null)}
          onSubmit={() => void handleRemoveProvider(confirmRemoveProvider)}
        />
      )}

      {confirmRemoveModel && (
        <Modal
          kind="confirm"
          title="Remove model?"
          message={`Remove ${confirmRemoveModel.modelId} from the registry?`}
          confirmLabel="Remove"
          danger
          onCancel={() => setConfirmRemoveModel(null)}
          onSubmit={() => void handleRemoveModel(confirmRemoveModel.modelId)}
        />
      )}
    </div>
  );
}
