import { useEffect, useState } from "react";
import {
  getHitlSettings,
  getRouting,
  putRouting,
  type HitlSettings,
} from "../api";

interface UseSettingsResult {
  hasModel: boolean | null;
  availableModels: string[];
  lastUsedModel: string;
  defaultModel: string;
  yoloMode: boolean;
  settingsRevision: number;
  bumpSettingsRevision: () => void;
  /** Persist the resolved model id and update last-used state. */
  persistUsedModel: (model: string) => void;
  /** Seed model for a new or existing session. */
  seedModel: (currentSelected?: string) => string;
}

export function useSettings(): UseSettingsResult {
  const [hasModel, setHasModel] = useState<boolean | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [lastUsedModel, setLastUsedModel] = useState<string>("");
  const [defaultModel, setDefaultModel] = useState<string>("");
  const [yoloMode, setYoloMode] = useState<boolean>(false);
  const [settingsRevision, setSettingsRevision] = useState(0);

  const bumpSettingsRevision = () => setSettingsRevision((r) => r + 1);

  // Refresh routing/model availability when settings change.
  useEffect(() => {
    let cancelled = false;
    getRouting()
      .then((r) => {
        if (!cancelled) {
          setHasModel((r.available_models?.length ?? 0) > 0);
          setAvailableModels(r.available_models ?? []);
          setLastUsedModel(r.last_used_model ?? "");
          setDefaultModel(r.default_model ?? "");
        }
      })
      .catch(() => {
        if (!cancelled) setHasModel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

  // Pull YOLO flag from the server. Refreshed on every settings
  // revision so toggling it inside the drawer updates the badge.
  useEffect(() => {
    let cancelled = false;
    getHitlSettings()
      .then((s: HitlSettings) => {
        if (!cancelled) setYoloMode(s.yolo_mode);
      })
      .catch(() => {
        // Backend doesn't speak /settings (older binary, or offline)
        // — hide the badge rather than crashing the layout.
        if (!cancelled) setYoloMode(false);
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

  const persistUsedModel = (model: string) => {
    if (model && model !== "auto") {
      setLastUsedModel(model);
      putRouting({ last_used_model: model }).catch(() => {});
    }
  };

  const seedModel = (currentSelected?: string): string => {
    const isReal = (s: string) => !!s && s !== "auto" && availableModels.includes(s);
    return (
      (currentSelected && isReal(currentSelected) ? currentSelected : undefined) ??
      (isReal(lastUsedModel) ? lastUsedModel : undefined) ??
      (isReal(defaultModel) ? defaultModel : undefined) ??
      availableModels[0] ??
      ""
    );
  };

  return {
    hasModel,
    availableModels,
    lastUsedModel,
    defaultModel,
    yoloMode,
    settingsRevision,
    bumpSettingsRevision,
    persistUsedModel,
    seedModel,
  };
}
