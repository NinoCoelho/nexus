import { useMemo, useState } from "react";
import type { ModelCapability, ProviderCatalogEntry } from "../../../api";
import { useToast } from "../../../toast/ToastProvider";

interface Props {
  catalog: ProviderCatalogEntry;
  selected: string[];
  onChange: (models: string[]) => void;
  onDiscover: () => Promise<string[]>;
  discovering: boolean;
}

type FilterMode = "chat" | "reasoning" | "tools" | "vision" | "all";

const FILTER_LABEL: Record<FilterMode, string> = {
  chat: "Chat",
  reasoning: "Reasoning",
  tools: "Tools",
  vision: "Vision",
  all: "All",
};

const FILTER_ORDER: FilterMode[] = ["chat", "reasoning", "tools", "vision", "all"];

/** Pattern of model-id substrings that almost always mean a non-chat
 *  endpoint — embeddings, speech, image generation, moderation. Used
 *  to filter the raw ``/v1/models`` discovery list when the active
 *  capability filter is anything except "all". OpenAI's models endpoint
 *  returns ~80 ids; this trims the noise so the chip palette stays
 *  scannable. */
const NON_CHAT_PATTERNS = [
  /embed/i,
  /\bwhisper\b/i,
  /\btts\b/i,
  /-tts-/i,
  /\bdall[- ]?e\b/i,
  /\bmoderation\b/i,
  /audio-preview/i,
  /\brealtime\b/i,
  /\bsearch-preview\b/i,
  /\binstruct\b.*\b(text|legacy)/i,
];

function isLikelyNonChat(id: string): boolean {
  return NON_CHAT_PATTERNS.some((re) => re.test(id));
}

/** Lightweight regex inference for discovered ids — when the catalog
 *  doesn't carry capability metadata for them. Conservative: any model
 *  whose name suggests reasoning ("o1", "o3", "reasoner", "thinking",
 *  "r1") gets the reasoning tag. Vision-aware names get vision. */
function inferCapabilities(id: string): ModelCapability[] {
  const out: ModelCapability[] = ["chat", "tools"];
  const lower = id.toLowerCase();
  if (
    /(^|[^a-z0-9])o[13](-mini|-pro)?([^a-z0-9]|$)/.test(lower) ||
    lower.includes("reasoner") ||
    lower.includes("thinking") ||
    lower.includes("-r1") ||
    lower.endsWith("-r1")
  ) {
    out.push("reasoning");
  }
  if (lower.includes("vision") || lower.includes("4o") || lower.includes("claude-3") || lower.includes("gemini")) {
    out.push("vision");
  }
  if (lower.includes("embed")) {
    return ["embedding"];
  }
  if (/\b(whisper|tts|audio|realtime)\b/.test(lower)) {
    return ["audio"];
  }
  return out;
}

export default function SelectModels({
  catalog,
  selected,
  onChange,
  onDiscover,
  discovering,
}: Props) {
  const toast = useToast();
  const [discovered, setDiscovered] = useState<string[] | null>(null);
  const [customInput, setCustomInput] = useState("");
  const [filter, setFilter] = useState<FilterMode>("chat");

  // Catalog-known capabilities — wins over inference when both have an
  // entry for the same id.
  const catalogCaps = useMemo(() => {
    const out: Record<string, ModelCapability[]> = {};
    for (const m of catalog.default_models) out[m.id] = m.capabilities;
    return out;
  }, [catalog.default_models]);

  const palette = useMemo(() => {
    const ids = Array.from(
      new Set<string>([
        ...catalog.default_models.map((m) => m.id),
        ...(discovered ?? []),
        ...selected,
      ]),
    );
    return ids.map((id) => ({
      id,
      capabilities: catalogCaps[id] ?? inferCapabilities(id),
    }));
  }, [catalog.default_models, discovered, selected, catalogCaps]);

  const filtered = useMemo(() => {
    if (filter === "all") return palette;
    return palette.filter((m) => {
      // The chat filter excludes embeddings + audio-only models — i.e. the
      // ones that have NO chat capability. Other filters require the tag.
      if (filter === "chat") {
        return m.capabilities.includes("chat") && !isLikelyNonChat(m.id);
      }
      return m.capabilities.includes(filter);
    });
  }, [palette, filter]);

  const selectedSet = new Set(selected);

  function toggle(modelId: string) {
    if (selectedSet.has(modelId)) onChange(selected.filter((m) => m !== modelId));
    else onChange([...selected, modelId]);
  }

  function addCustom() {
    const m = customInput.trim();
    if (!m) return;
    if (!selectedSet.has(m)) onChange([...selected, m]);
    setCustomInput("");
  }

  async function refresh() {
    try {
      const models = await onDiscover();
      setDiscovered(models);
      if (models.length === 0) toast.info("Provider returned no models.");
    } catch (e) {
      toast.error("Could not list models.", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  const hiddenCount = palette.length - filtered.length;

  return (
    <div className="provider-wizard-step provider-wizard-step--models">
      <h3 className="provider-wizard-step__title">Pick models</h3>
      <p className="provider-wizard-step__subtitle">
        Filter by what the model can do. Catalog-curated lists already exclude embedding-only and audio models.
      </p>

      <div className="provider-wizard-filter-row">
        {FILTER_ORDER.map((f) => (
          <button
            key={f}
            type="button"
            className={`provider-wizard-chip${
              filter === f ? " provider-wizard-chip--on" : ""
            }`}
            onClick={() => setFilter(f)}
          >
            {FILTER_LABEL[f]}
          </button>
        ))}
        {hiddenCount > 0 && (
          <span className="provider-wizard-filter-hint">
            {hiddenCount} hidden by filter
          </span>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="provider-wizard-empty">
          No models match the “{FILTER_LABEL[filter]}” filter. Try a different filter, refresh from the provider, or type a model id.
        </p>
      ) : (
        <div className="provider-wizard-chips">
          {filtered.map((m) => (
            <button
              key={m.id}
              type="button"
              className={`provider-wizard-chip${
                selectedSet.has(m.id) ? " provider-wizard-chip--on" : ""
              }`}
              onClick={() => toggle(m.id)}
              title={m.capabilities.join(" · ")}
            >
              {m.id}
            </button>
          ))}
        </div>
      )}

      <div className="provider-wizard-models-actions">
        <button
          type="button"
          className="provider-wizard-secondary-btn"
          onClick={() => void refresh()}
          disabled={discovering}
        >
          {discovering ? "Discovering…" : "Refresh from provider"}
        </button>
        <div className="provider-wizard-custom-row">
          <input
            className="form-input"
            placeholder="Add a model id (e.g. gpt-4o-2024-08-06)…"
            value={customInput}
            onChange={(e) => setCustomInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustom();
              }
            }}
          />
          <button
            type="button"
            className="provider-wizard-secondary-btn"
            onClick={addCustom}
            disabled={!customInput.trim()}
          >
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
