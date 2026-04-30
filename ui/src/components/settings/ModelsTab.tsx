import { useTranslation } from "react-i18next";
import type { Model, Provider, RoutingConfig } from "../../api";
import LocalModels from "../LocalModels";
import ModelsSection from "../ModelsSection";
import ProvidersList from "./ProvidersList";
import SettingsSection from "./SettingsSection";

interface Props {
  routing: RoutingConfig | null;
  providers: Provider[];
  models: Model[];
  onRefresh: () => void;
}

export default function ModelsTab({ routing, providers, models, onRefresh }: Props) {
  const { t } = useTranslation("settings");
  return (
    <>
      <SettingsSection
        title={t("settings:modelsTab.cloudProvidersTitle")}
        icon={t("settings:modelsTab.cloudProvidersIcon")}
        collapsible
        help={{
          title: t("settings:modelsTab.cloudProvidersHelpTitle"),
          body: (
            <>
              A provider is a service (OpenAI, Anthropic, Ollama, etc.) that
              hosts models. To use a cloud model you need an API key from the
              provider. Ollama and local servers don't need a key.
            </>
          ),
        }}
      >
        <ProvidersList providers={providers} models={models} onRefresh={onRefresh} />
      </SettingsSection>

      <SettingsSection
        title={t("settings:modelsTab.localModelsTitle")}
        icon={t("settings:modelsTab.localModelsIcon")}
        collapsible
        defaultOpen={false}
        help={{
          title: t("settings:modelsTab.localModelsHelpTitle"),
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
        title={t("settings:modelsTab.manageModelsTitle")}
        icon={t("settings:modelsTab.manageModelsIcon")}
        collapsible
        help={{
          title: t("settings:modelsTab.manageModelsHelpTitle"),
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
