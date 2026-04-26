import { useState } from "react";
import { putRouting, type Model, type RoutingConfig } from "../../api";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  routing: RoutingConfig | null;
  models: Model[];
  onChanged: () => void;
}

export default function DefaultModelStrip({ routing, models, onChanged }: Props) {
  const toast = useToast();
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);

  const current = routing?.default_model ?? "";
  const currentModel = models.find((m) => m.id === current);

  async function setDefault(id: string) {
    setSaving(true);
    try {
      await putRouting({ default_model: id });
      toast.success(id ? `Modelo padrão: ${id}` : "Modelo padrão removido");
      setPicking(false);
      onChanged();
    } catch (e) {
      toast.error("Falha ao salvar modelo padrão", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="s-default-strip">
      <span className="s-default-strip__icon" aria-hidden>★</span>
      <div className="s-default-strip__text">
        <span className="s-default-strip__label">Modelo padrão</span>
        {picking ? (
          <select
            className="s-select"
            autoFocus
            disabled={saving}
            value={current}
            onChange={(e) => void setDefault(e.target.value)}
            onBlur={() => setPicking(false)}
          >
            <option value="">— nenhum —</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        ) : current ? (
          <span className="s-default-strip__value" title={current}>
            {current}
            {currentModel && (
              <span style={{ color: "var(--fg-faint)", fontFamily: "inherit", fontWeight: 400 }}>
                {" · "}
                {currentModel.provider}
              </span>
            )}
          </span>
        ) : (
          <span className="s-default-strip__value s-default-strip__value--empty">
            nenhum modelo escolhido
          </span>
        )}
      </div>
      {!picking && (
        <button
          type="button"
          className="s-default-strip__btn"
          onClick={() => setPicking(true)}
          disabled={saving || models.length === 0}
          title={models.length === 0 ? "Adicione um modelo primeiro" : "Trocar modelo padrão"}
        >
          Trocar
        </button>
      )}
    </div>
  );
}
