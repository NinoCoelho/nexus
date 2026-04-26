import { useState } from "react";
import { putRouting, type Model, type Provider, type RoutingConfig } from "../../api";
import { useToast } from "../../toast/ToastProvider";
import AppearanceSection from "../AppearanceSection";
import SettingsSection from "./SettingsSection";

interface Props {
  routing: RoutingConfig | null;
  models: Model[];
  providers: Provider[];
  onChanged: () => void;
}

export default function QuickStartTab({ routing, models, providers, onChanged }: Props) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);

  const current = routing?.default_model ?? "";
  const currentModel = models.find((m) => m.id === current);

  async function setDefault(id: string) {
    setSaving(true);
    try {
      await putRouting({ default_model: id });
      toast.success(id ? `Modelo padrão: ${id}` : "Modelo padrão removido");
      onChanged();
    } catch (e) {
      toast.error("Falha ao salvar", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setSaving(false);
    }
  }

  const connectedProviders = providers.filter((p) => p.has_key || p.type === "ollama").length;
  const totalModels = models.length;

  return (
    <>
      <SettingsSection
        title="Modelo padrão"
        icon="★"
        description="Este é o modelo usado quando você inicia uma nova conversa, sem escolher outro. Você pode trocar a qualquer momento no chat."
      >
        <div className="s-quick-card">
          <select
            className="s-select"
            value={current}
            disabled={saving || models.length === 0}
            onChange={(e) => void setDefault(e.target.value)}
          >
            <option value="">— escolher um modelo —</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id} ({m.provider})
              </option>
            ))}
          </select>
          {models.length === 0 ? (
            <p className="s-quick-card__desc">
              Nenhum modelo cadastrado. Vá em <b>Modelos</b> para adicionar um modelo
              cloud (OpenAI, Anthropic, etc.) ou instalar um localmente.
            </p>
          ) : currentModel ? (
            <p className="s-quick-card__desc">
              Em uso: <b>{currentModel.id}</b> via <b>{currentModel.provider}</b>
              {currentModel.notes && <> — {currentModel.notes}</>}
            </p>
          ) : (
            <p className="s-quick-card__desc">
              Selecione um modelo acima para definir como padrão.
            </p>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title="Aparência" icon="🎨">
        <AppearanceSection />
      </SettingsSection>

      <SettingsSection title="Status" icon="✓">
        <div className="s-quick-status">
          <div className="s-quick-status__row">
            <span
              className={`s-quick-status__dot s-quick-status__dot--${
                connectedProviders > 0 ? "ok" : "warn"
              }`}
            />
            {connectedProviders > 0
              ? `${connectedProviders} ${connectedProviders === 1 ? "provedor conectado" : "provedores conectados"}`
              : "Nenhum provedor conectado"}
          </div>
          <div className="s-quick-status__row">
            <span
              className={`s-quick-status__dot s-quick-status__dot--${
                totalModels > 0 ? "ok" : "warn"
              }`}
            />
            {totalModels > 0
              ? `${totalModels} ${totalModels === 1 ? "modelo disponível" : "modelos disponíveis"}`
              : "Nenhum modelo cadastrado"}
          </div>
          <div className="s-quick-status__row">
            <span
              className={`s-quick-status__dot s-quick-status__dot--${
                current ? "ok" : "warn"
              }`}
            />
            {current ? "Modelo padrão definido" : "Modelo padrão não definido"}
          </div>
        </div>
      </SettingsSection>
    </>
  );
}
