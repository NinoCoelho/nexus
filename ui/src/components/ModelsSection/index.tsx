/**
 * ModelsSection — model configuration UI for the settings drawer.
 *
 * Models carry a single routing hint (`tier`) + optional free-text `notes`.
 * Tier auto-fills from the model name when adding; the user can override.
 */

import { useCallback, useEffect, useState } from "react";
import {
  clearModelRole,
  deleteModel,
  postModel,
  patchModel,
  fetchProviderModels,
  putRouting,
  setModelRole,
  suggestModelTier,
  type Model,
  type Provider,
  type RoutingConfig,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import ModelRow from "./ModelRow";
import ModelFormPanel from "./ModelFormPanel";
import {
  EMBEDDING_COMPAT_TYPES,
  CACHE_TTL_MS,
  emptyForm,
  type ModelForm,
  type DiscoveryState,
} from "./types";

interface Props {
  models: Model[];
  providers: Provider[];
  routing: RoutingConfig | null;
  onRefresh: () => void;
}

export default function ModelsSection({ models, providers, routing, onRefresh }: Props) {
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ModelForm>(emptyForm);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<Record<string, DiscoveryState>>({});
  const [fetching, setFetching] = useState(false);
  const [filter, setFilter] = useState("");
  const [roleSaving, setRoleSaving] = useState(false);

  const embModelId = routing?.embedding_model_id ?? "";
  const extModelId = routing?.extraction_model_id ?? "";
  const defaultModelId = routing?.default_model ?? "";

  const providerTypeMap = Object.fromEntries(providers.map((p) => [p.name, p.type ?? "openai_compat"]));

  function getModelRoles(m: Model): string[] {
    const roles: string[] = [];
    if (embModelId === m.id) roles.push("embedding");
    if (extModelId === m.id) roles.push("extraction");
    return roles;
  }

  function canEmbed(m: Model): boolean {
    const ptype = providerTypeMap[m.provider];
    return EMBEDDING_COMPAT_TYPES.has(ptype) && !!m.is_embedding_capable;
  }

  function hasRole(m: Model): boolean {
    return embModelId === m.id || extModelId === m.id;
  }

  async function assignRole(role: string, modelId: string) {
    setRoleSaving(true);
    try {
      await setModelRole(role, modelId);
      onRefresh();
    } catch (e) {
      toast.error("Failed to assign role", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setRoleSaving(false);
    }
  }

  async function unassignRole(role: string) {
    setRoleSaving(true);
    try {
      await clearModelRole(role);
      onRefresh();
    } catch (e) {
      toast.error("Failed to clear role", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setRoleSaving(false);
    }
  }

  async function setDefault(modelId: string) {
    setRoleSaving(true);
    try {
      await putRouting({ default_model: modelId });
      toast.success(`Default model set to ${modelId}`);
      onRefresh();
    } catch (e) {
      toast.error("Failed to set default", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setRoleSaving(false);
    }
  }

  async function clearDefault() {
    setRoleSaving(true);
    try {
      await putRouting({ default_model: "" });
      onRefresh();
    } catch (e) {
      toast.error("Failed to clear default", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setRoleSaving(false);
    }
  }

  async function removeModel(id: string) {
    try {
      await deleteModel(id);
      setConfirmRemove(null);
      toast.success(`Removed ${id}`);
      onRefresh();
    } catch (e) {
      toast.error("Remove failed", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  const currentDiscovery = form.provider ? discovery[form.provider] : undefined;
  const fetchedModels = currentDiscovery?.models ?? [];

  const doFetchModels = useCallback(async (provider: string, force = false) => {
    if (!provider) return;
    const cached = discovery[provider];
    if (!force && cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) return;
    setFetching(true);
    try {
      const result = await fetchProviderModels(provider);
      setDiscovery((c) => ({
        ...c,
        [provider]: {
          models: result.ok ? result.models : [],
          fetchedAt: Date.now(),
          error: result.ok ? null : (result.error || "Failed to fetch models"),
        },
      }));
    } catch (e) {
      setDiscovery((c) => ({
        ...c,
        [provider]: {
          models: [],
          fetchedAt: Date.now(),
          error: e instanceof Error ? e.message : "Fetch failed",
        },
      }));
    } finally {
      setFetching(false);
    }
  }, [discovery]);

  // When the model_name changes in add-mode, fetch a tier suggestion.
  useEffect(() => {
    if (editingId) return; // editing keeps whatever was there
    const name = form.model_name.trim();
    if (!name) return;
    let cancelled = false;
    suggestModelTier(name).then((res) => {
      if (cancelled) return;
      // Only auto-apply if the user hasn't manually touched it yet.
      setForm((f) => {
        if (f.tier_source === "manual") return f;
        return { ...f, tier: res.tier, tier_source: res.source === "heuristic" ? "heuristic" : "default" };
      });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [form.model_name, editingId]);

  function pickModel(upstreamName: string) {
    setForm((f) => ({
      ...f,
      model_name: upstreamName,
      id: f.id_touched ? f.id : `${f.provider}/${upstreamName}`,
    }));
  }

  function openEdit(m: Model) {
    setEditingId(m.id);
    setAdding(false);
    setForm({
      id: m.id,
      id_touched: true,
      provider: m.provider,
      model_name: m.model_name,
      tags: m.tags.join(", "),
      tier: m.tier,
      notes: m.notes ?? "",
      tier_source: "manual",
      is_embedding_capable: !!m.is_embedding_capable,
      context_window: m.context_window && m.context_window > 0 ? String(m.context_window) : "",
    });
  }

  function cancelForm() {
    setAdding(false);
    setEditingId(null);
    setForm(emptyForm);
    setFilter("");
  }

  async function saveModel() {
    const ctxParsed = form.context_window.trim() === "" ? 0 : Number(form.context_window);
    if (Number.isNaN(ctxParsed) || ctxParsed < 0) {
      toast.error("Context size must be a non-negative integer");
      return;
    }
    if (editingId) {
      try {
        await patchModel(editingId, {
          model_name: form.model_name.trim(),
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
          tier: form.tier,
          notes: form.notes,
          is_embedding_capable: form.is_embedding_capable,
          context_window: ctxParsed,
        });
        toast.success(`Updated ${editingId}`);
        cancelForm();
        onRefresh();
      } catch (e) {
        toast.error("Update failed", { detail: e instanceof Error ? e.message : undefined });
      }
      return;
    }
    if (!form.id.trim() || !form.provider || !form.model_name.trim()) return;
    try {
      await postModel({
        id: form.id.trim(),
        provider: form.provider,
        model_name: form.model_name.trim(),
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        tier: form.tier,
        notes: form.notes,
        is_embedding_capable: form.is_embedding_capable,
        context_window: ctxParsed > 0 ? ctxParsed : undefined,
      });
      const id = form.id.trim();
      toast.success(`Added model ${id}`);
      cancelForm();
      onRefresh();
    } catch (e) {
      toast.error("Add failed", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  const visibleFetched = filter.trim()
    ? fetchedModels.filter((m) => m.toLowerCase().includes(filter.trim().toLowerCase()))
    : fetchedModels;

  const discoveryError = currentDiscovery?.error ?? null;
  const usingBuiltinEmbedder = !embModelId;

  return (
    <div className="settings-section">
      <div className="settings-section-label">
        Models {roleSaving && <span style={{ color: "var(--fg-faint)", fontWeight: 400 }}>· saving…</span>}
      </div>
      {usingBuiltinEmbedder && (
        <div style={{ fontSize: 11, color: "var(--fg-faint)", marginBottom: 8 }}>
          GraphRAG is using the built-in local embedder
          (sentence-transformers/all-MiniLM-L6-v2, 384-dim) and the spaCy NER extractor.
          Assign a model to <b>Embedding</b> below to override.
        </div>
      )}

      {models.map((m) => {
        if (editingId === m.id) return null;
        return (
          <ModelRow
            key={m.id}
            model={m}
            roles={getModelRoles(m)}
            canEmbed={canEmbed(m)}
            isDefault={defaultModelId === m.id}
            locked={hasRole(m)}
            confirmRemove={confirmRemove}
            roleSaving={roleSaving}
            onEdit={() => openEdit(m)}
            onRemove={() => void removeModel(m.id)}
            onConfirmRemove={() => setConfirmRemove(m.id)}
            onCancelRemove={() => setConfirmRemove(null)}
            onAssignRole={assignRole}
            onUnassignRole={unassignRole}
            onSetDefault={(id) => void setDefault(id)}
            onClearDefault={() => void clearDefault()}
          />
        );
      })}

      {(adding || editingId) ? (
        <ModelFormPanel
          form={form}
          editingId={editingId}
          providers={providers}
          fetchedModels={fetchedModels}
          visibleFetched={visibleFetched}
          filter={filter}
          fetching={fetching}
          discoveryError={discoveryError}
          onFormChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
          onProviderChange={(provider) => {
            setForm((f) => ({ ...f, provider }));
            setFilter("");
          }}
          onPickModel={pickModel}
          onFilterChange={setFilter}
          onFetchModels={doFetchModels}
          onCancel={cancelForm}
          onSave={() => void saveModel()}
        />
      ) : (
        <button className="settings-add-btn" onClick={() => { setAdding(true); setEditingId(null); setForm(emptyForm); }}>
          + Add model
        </button>
      )}
    </div>
  );
}
