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
        title="Transcrição de voz"
        icon="🎙"
        collapsible
        defaultOpen
        help={{
          title: "Transcrição",
          body: (
            <>
              Converte áudio que você grava em texto antes de enviar ao agente.
              No modo <b>local</b>, usa faster-whisper (sem internet). No modo{" "}
              <b>remoto</b>, envia o áudio para um endpoint compatível com a API
              de transcrição da OpenAI.
            </>
          ),
        }}
      >
        <TranscriptionSection />
      </SettingsSection>

      <SettingsSection
        title="Busca na web"
        icon="🔍"
        collapsible
        defaultOpen={false}
        help={{
          title: "Busca na web",
          body: (
            <>
              Permite que o agente pesquise na internet quando precisar. Você pode
              habilitar múltiplos provedores; o agente escolhe automaticamente.
            </>
          ),
        }}
      >
        <SearchSection />
      </SettingsSection>

      {graphStats && (
        <SettingsSection
          title="Grafo de conhecimento"
          icon="🕸"
          collapsible
          defaultOpen={false}
          help={{
            title: "Grafo de conhecimento (GraphRAG)",
            body: (
              <>
                Indexa seus arquivos do vault como entidades e relações para que o
                agente possa fazer perguntas conectadas (ex.: "o que esse projeto
                tem a ver com aquela decisão?"). Reindexação pode ser lenta em
                vaults grandes.
              </>
            ),
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="graphrag-stats-row">
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.entities}</span>
                <span className="graphrag-stat-label">entidades</span>
              </div>
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.triples}</span>
                <span className="graphrag-stat-label">relações</span>
              </div>
              <div className="graphrag-stat">
                <span className="graphrag-stat-value">{graphStats.component_count ?? 0}</span>
                <span className="graphrag-stat-label">componentes</span>
              </div>
            </div>
            <button
              className="settings-btn settings-btn--primary"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setReindexOpen(true)}
            >
              Atualizar índice
            </button>
          </div>
        </SettingsSection>
      )}

      <ReindexModal open={reindexOpen} onClose={() => setReindexOpen(false)} />
    </>
  );
}
