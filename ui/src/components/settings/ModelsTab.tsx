import type { Model, Provider, RoutingConfig } from "../../api";
import LocalModels from "../LocalModels";
import ModelsSection from "../ModelsSection";
import ProvidersSection from "../ProvidersSection";
import SettingsSection from "./SettingsSection";

interface Props {
  routing: RoutingConfig | null;
  providers: Provider[];
  models: Model[];
  onRefresh: () => void;
}

export default function ModelsTab({ routing, providers, models, onRefresh }: Props) {
  return (
    <>
      <SettingsSection
        title="Provedores cloud"
        icon="☁"
        collapsible
        defaultOpen
        help={{
          title: "O que é um provedor?",
          body: (
            <>
              Um provedor é um serviço (OpenAI, Anthropic, Ollama, etc.) que hospeda
              os modelos. Para usar um modelo cloud você precisa de uma chave de API
              do provedor. Ollama e servidores locais não precisam de chave.
            </>
          ),
        }}
      >
        <ProvidersSection providers={providers} onRefresh={onRefresh} />
      </SettingsSection>

      <SettingsSection
        title="Modelos no dispositivo"
        icon="💻"
        collapsible
        defaultOpen={false}
        help={{
          title: "Modelos no dispositivo",
          body: (
            <>
              Modelos rodando localmente via llama.cpp ou Ollama. Funcionam sem
              internet e sem cobrança por uso, mas dependem do hardware da sua
              máquina (RAM, GPU).
            </>
          ),
        }}
      >
        <LocalModels onRefresh={onRefresh} />
      </SettingsSection>

      <SettingsSection
        title="Gerenciar modelos"
        icon="📋"
        collapsible
        defaultOpen
        help={{
          title: "Modelos cadastrados",
          body: (
            <>
              Lista de modelos disponíveis para o agente. Cada modelo aponta para
              um provedor + nome upstream. <b>Padrão</b> é o modelo escolhido para
              novas conversas (configure na tira do topo). <b>Funções avançadas</b>{" "}
              (Embedding/Extração) só aparecem se você usa GraphRAG.
            </>
          ),
        }}
      >
        <ModelsSection
          models={models}
          providers={providers}
          routing={routing}
          onRefresh={onRefresh}
        />
      </SettingsSection>
    </>
  );
}
