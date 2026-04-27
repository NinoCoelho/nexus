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
        title="Cloud providers"
        icon="☁"
        collapsible
        defaultOpen
        help={{
          title: "What is a provider?",
          body: (
            <>
              A provider is a service (OpenAI, Anthropic, Ollama, etc.) that
              hosts models. To use a cloud model you need an API key from the
              provider. Ollama and local servers don't need a key.
            </>
          ),
        }}
      >
        <ProvidersSection providers={providers} onRefresh={onRefresh} />
      </SettingsSection>

      <SettingsSection
        title="On-device models"
        icon="💻"
        collapsible
        defaultOpen={false}
        help={{
          title: "On-device models",
          body: (
            <>
              Models running locally via llama.cpp or Ollama. They work
              without internet and without per-use billing, but depend on
              your machine's hardware (RAM, GPU).
            </>
          ),
        }}
      >
        <LocalModels onRefresh={onRefresh} />
      </SettingsSection>

      <SettingsSection
        title="Manage models"
        icon="📋"
        collapsible
        defaultOpen
        help={{
          title: "Registered models",
          body: (
            <>
              List of models available to the agent. Each model points to a
              provider + upstream name. <b>Default</b> is the model chosen
              for new conversations (set it in the top strip).{" "}
              <b>Advanced roles</b> (Embedding/Extraction) only appear if
              you use GraphRAG.
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
