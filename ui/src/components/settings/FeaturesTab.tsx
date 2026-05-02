import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type KnowledgeStats } from "../../api";
import ReindexModal from "../ReindexModal";
import SearchSection from "../SearchSection";
import TranscriptionSection from "../TranscriptionSection";
import VaultHistorySection from "../VaultHistorySection";
import SettingsSection from "./SettingsSection";
import SharingSection from "./SharingSection";
import VoiceSection from "./VoiceSection";

interface Props {
  graphStats: KnowledgeStats | null;
}

export default function FeaturesTab({ graphStats }: Props) {
  const { t } = useTranslation("settings");
  const [reindexOpen, setReindexOpen] = useState(false);

  return (
    <>
      <SettingsSection
        title={t("settings:features.transcriptionTitle")}
        icon={t("settings:features.transcriptionIcon")}
        collapsible
        help={{
          title: t("settings:features.transcriptionHelpTitle"),
          body: (
            <>
              Converts audio you record into text before sending it to the agent.
              In <b>local</b> mode, uses faster-whisper (no internet). In{" "}
              <b>remote</b> mode, sends the audio to an endpoint compatible with
              OpenAI's transcription API.
            </>
          ),
        }}
      >
        <TranscriptionSection />
      </SettingsSection>

      <SettingsSection
        title="Voice & speech"
        icon="🔊"
        collapsible
        defaultOpen={false}
        help={{
          title: "Voice output",
          body: (
            <>
              Read assistant messages and vault notes aloud, and (for voice
              messages) get a brief spoken acknowledgment when the agent
              starts working, while it's running, and when it finishes.
              Engines: <b>Web Speech</b> uses your browser's built-in
              voices (zero install). <b>Piper</b> runs a small ONNX model
              locally for higher quality (downloads on first use). Remote
              options (OpenAI, ElevenLabs) require an API key.
            </>
          ),
        }}
      >
        <VoiceSection />
      </SettingsSection>

      <SettingsSection
        title={t("settings:features.webSearchTitle")}
        icon={t("settings:features.webSearchIcon")}
        collapsible
        defaultOpen={false}
        help={{
          title: t("settings:features.webSearchHelpTitle"),
          body: (
            <>
              Lets the agent search the web when it needs to. You can enable
              multiple providers; the agent picks automatically.
            </>
          ),
        }}
      >
        <SearchSection />
      </SettingsSection>

      {graphStats && (
        <SettingsSection
          title={t("settings:features.knowledgeGraphTitle")}
          icon={t("settings:features.knowledgeGraphIcon")}
          collapsible
          defaultOpen={false}
          help={{
            title: t("settings:features.knowledgeGraphHelpTitle"),
            body: (
              <>
                Indexes your vault files as entities and relations so the agent
                can answer connected questions (e.g. "how does this project
                relate to that decision?"). Reindexing can be slow on large
                vaults.
              </>
            ),
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="graphrag-stats-row">
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.entities}</span>
                <span className="graphrag-stat-label">{t("settings:features.entities")}</span>
              </div>
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.triples}</span>
                <span className="graphrag-stat-label">{t("settings:features.relations")}</span>
              </div>
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.component_count ?? 0}</span>
                <span className="graphrag-stat-label">{t("settings:features.components")}</span>
              </div>
            </div>
            <button
              className="settings-btn settings-btn--primary"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setReindexOpen(true)}
            >
              {t("settings:features.reindexButton")}
            </button>
          </div>
        </SettingsSection>
      )}

      <SettingsSection
        title={t("settings:features.vaultHistoryTitle")}
        icon={t("settings:features.vaultHistoryIcon")}
        collapsible
        defaultOpen={false}
        help={{
          title: t("settings:features.vaultHistoryHelpTitle"),
          body: (
            <>
              Opt-in: when enabled, every vault save is committed to a
              private git repo at <code>~/.nexus/.vault-history</code>.
              Right-click any file or folder in the tree to undo the most
              recent change. Disabling preserves the existing history.
            </>
          ),
        }}
      >
        <VaultHistorySection />
      </SettingsSection>

      <SharingSection />

      <ReindexModal open={reindexOpen} onClose={() => setReindexOpen(false)} />
    </>
  );
}
