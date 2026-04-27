import { useState } from "react";
import { type KnowledgeStats } from "../../api";
import ReindexModal from "../ReindexModal";
import SearchSection from "../SearchSection";
import TranscriptionSection from "../TranscriptionSection";
import SettingsSection from "./SettingsSection";

interface Props {
  graphStats: KnowledgeStats | null;
}

export default function FeaturesTab({ graphStats }: Props) {
  const [reindexOpen, setReindexOpen] = useState(false);

  return (
    <>
      <SettingsSection
        title="Voice transcription"
        icon="🎙"
        collapsible
        defaultOpen
        help={{
          title: "Transcription",
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
        title="Web search"
        icon="🔍"
        collapsible
        defaultOpen={false}
        help={{
          title: "Web search",
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
          title="Knowledge graph"
          icon="🕸"
          collapsible
          defaultOpen={false}
          help={{
            title: "Knowledge graph (GraphRAG)",
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
                <span className="graphrag-stat-label">entities</span>
              </div>
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.triples}</span>
                <span className="graphrag-stat-label">relations</span>
              </div>
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.component_count ?? 0}</span>
                <span className="graphrag-stat-label">components</span>
              </div>
            </div>
            <button
              className="settings-btn settings-btn--primary"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setReindexOpen(true)}
            >
              Reindex
            </button>
          </div>
        </SettingsSection>
      )}

      <ReindexModal open={reindexOpen} onClose={() => setReindexOpen(false)} />
    </>
  );
}
