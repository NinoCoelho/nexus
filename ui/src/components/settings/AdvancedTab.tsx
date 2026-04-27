import { useEffect, useState } from "react";
import {
  AGENT_DEFAULTS,
  getConfig,
  patchAgentConfig,
  setHitlSettings,
  type AgentConfig,
  type HitlSettings,
} from "../../api";
import {
  SOUND_KEYS,
  SOUND_LABELS,
  soundToneCount,
  sounds,
  useSoundMute,
  useSoundVolumes,
} from "../../hooks/useSounds";
import { useToast } from "../../toast/ToastProvider";
import NumberFieldWithDefault from "./NumberFieldWithDefault";
import SettingsField from "./SettingsField";
import SettingsSection from "./SettingsSection";

interface Props {
  hitl: HitlSettings | null;
  onHitlChanged: (next: HitlSettings) => void;
}

export default function AdvancedTab({ hitl, onHitlChanged }: Props) {
  const toast = useToast();
  const { muted: soundMuted, setMuted: setSoundMuted } = useSoundMute();
  const { volumes: soundVolumes, setVolume: setSoundVolume } = useSoundVolumes();
  const [agent, setAgent] = useState<AgentConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [hitlSaving, setHitlSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getConfig()
      .then((c) => {
        if (!cancelled) setAgent(c.agent);
      })
      .catch((e) => {
        toast.error("Falha ao carregar configurações avançadas", {
          detail: e instanceof Error ? e.message : undefined,
        });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [toast]);

  async function patchAgent(patch: Partial<AgentConfig>) {
    setAgent((prev) => (prev ? { ...prev, ...patch } : prev));
    try {
      const next = await patchAgentConfig(patch);
      setAgent(next.agent);
      toast.success("Configuração salva");
    } catch (e) {
      toast.error("Falha ao salvar", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function toggleYolo() {
    if (!hitl) return;
    setHitlSaving(true);
    const next = !hitl.yolo_mode;
    try {
      const updated = await setHitlSettings({ yolo_mode: next });
      onHitlChanged(updated);
      toast.success(next ? "Aprovação automática ativada" : "Aprovação automática desativada");
    } catch (e) {
      toast.error("Falha ao salvar", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setHitlSaving(false);
    }
  }

  return (
    <>
      <SettingsSection
        title="Comportamento do agente"
        icon="⚙"
        description="Ajustes finos de geração. Os padrões funcionam bem na maioria dos casos."
      >
        {loading && <p className="s-field__hint">Carregando…</p>}
        {agent && (
          <>
            <SettingsField
              label="Máximo de passos por turno"
              hint="Limite de iterações da máquina de tools antes de parar. Valores altos permitem tarefas complexas, baixos protegem contra loops."
              help={{
                title: "Máximo de passos por turno",
                body: (
                  <>
                    Cada chamada do agente pode envolver múltiplas idas e voltas:
                    chamar uma ferramenta, ler o resultado, decidir o próximo passo.
                    Esse limite (padrão {AGENT_DEFAULTS.max_iterations}) impede que o
                    agente fique preso num loop. Aumente se você usa o agente para
                    tarefas longas (pesquisa, refactor); diminua para limitar custo.
                  </>
                ),
              }}
              layout="row"
            >
              <NumberFieldWithDefault
                value={agent.max_iterations ?? AGENT_DEFAULTS.max_iterations}
                defaultValue={AGENT_DEFAULTS.max_iterations}
                min={1}
                max={100}
                onCommit={(v) => void patchAgent({ max_iterations: v })}
              />
            </SettingsField>

            <SettingsField
              label="Temperatura"
              hint="0 = determinístico (recomendado para tool-calling). Valores altos aumentam criatividade ao custo de saídas instáveis."
              help={{
                title: "Temperatura",
                body: (
                  <>
                    Controla aleatoriedade do modelo. Em <b>0</b>, o modelo escolhe
                    sempre o token mais provável — ideal quando o agente precisa
                    chamar ferramentas com argumentos JSON corretos. Acima de 0.7,
                    as respostas viram criativas mas a estrutura JSON pode quebrar.
                    A maioria dos usuários deve deixar em 0.
                  </>
                ),
              }}
              layout="row"
            >
              <NumberFieldWithDefault
                value={agent.temperature ?? AGENT_DEFAULTS.temperature}
                defaultValue={AGENT_DEFAULTS.temperature}
                min={0}
                max={2}
                step={0.1}
                onCommit={(v) => void patchAgent({ temperature: v })}
              />
            </SettingsField>

            <SettingsField
              label="Penalidade de frequência"
              hint="Reduz a chance de tokens já usados aparecerem de novo. Útil contra modelos locais que travam em loops."
              help={{
                title: "Penalidade de frequência",
                body: (
                  <>
                    Modelos locais (deepseek-coder, certas versões de llama) podem
                    cair em degeneração — repetindo "@@@@…" infinitamente. Esse
                    parâmetro penaliza tokens já usados e reduz drasticamente esse
                    problema. Padrão: {AGENT_DEFAULTS.frequency_penalty}.
                  </>
                ),
              }}
              layout="row"
            >
              <NumberFieldWithDefault
                value={agent.frequency_penalty ?? AGENT_DEFAULTS.frequency_penalty}
                defaultValue={AGENT_DEFAULTS.frequency_penalty}
                min={-2}
                max={2}
                step={0.1}
                onCommit={(v) => void patchAgent({ frequency_penalty: v })}
              />
            </SettingsField>

            <SettingsField
              label="Penalidade de presença"
              hint="Empurra o modelo a tocar em assuntos novos. Padrão 0 (sem efeito)."
              help={{
                title: "Penalidade de presença",
                body: (
                  <>
                    Diferente da penalidade de frequência (que olha quantas vezes
                    cada token apareceu), esta penaliza qualquer token que tenha
                    aparecido pelo menos uma vez. Use valores positivos para
                    encorajar tópicos novos. Padrão: {AGENT_DEFAULTS.presence_penalty}.
                  </>
                ),
              }}
              layout="row"
            >
              <NumberFieldWithDefault
                value={agent.presence_penalty ?? AGENT_DEFAULTS.presence_penalty}
                defaultValue={AGENT_DEFAULTS.presence_penalty}
                min={-2}
                max={2}
                step={0.1}
                onCommit={(v) => void patchAgent({ presence_penalty: v })}
              />
            </SettingsField>

            <SettingsField
              label="Detector de loop infinito"
              hint="Aborta o stream se a saída ficar repetindo o mesmo padrão por N caracteres. 0 desativa."
              help={{
                title: "Detector de loop infinito",
                body: (
                  <>
                    Salvaguarda contra modelos que entram em loop emitindo o mesmo
                    padrão para sempre. Se o final da resposta for um padrão de até
                    8 caracteres repetido por <b>N</b> caracteres, o stream é
                    cortado com finish_reason=stop. Padrão: {AGENT_DEFAULTS.anti_repeat_threshold}.
                    Ponha 0 para desativar (não recomendado).
                  </>
                ),
              }}
              layout="row"
            >
              <NumberFieldWithDefault
                value={agent.anti_repeat_threshold ?? AGENT_DEFAULTS.anti_repeat_threshold}
                defaultValue={AGENT_DEFAULTS.anti_repeat_threshold}
                min={0}
                max={2000}
                step={10}
                onCommit={(v) => void patchAgent({ anti_repeat_threshold: v })}
              />
            </SettingsField>
          </>
        )}
      </SettingsSection>

      <SettingsSection
        title="Aprovação automática"
        icon="🤖"
        description="Quando uma ferramenta perigosa pede confirmação antes de executar, o agente normalmente abre uma caixa de diálogo. Com a aprovação automática ativada, perguntas do tipo sim/não são respondidas automaticamente."
      >
        {hitl && (
          <SettingsField
            label="Aprovar perguntas sim/não automaticamente"
            hint="Não afeta perguntas de texto livre nem de múltipla escolha — você ainda terá que responder essas."
            help={{
              title: "Aprovação automática (YOLO)",
              body: (
                <>
                  O agente pergunta antes de executar comandos potencialmente
                  perigosos (apagar arquivo, rodar shell, etc.). Ativar este modo
                  significa que ele <b>não</b> vai esperar sua confirmação para
                  perguntas binárias. Use só se você confia no agente e revisou
                  bem suas skills.
                </>
              ),
            }}
            layout="row"
          >
            <button
              type="button"
              role="switch"
              aria-checked={hitl.yolo_mode}
              className={`hitl-switch ${hitl.yolo_mode ? "on" : "off"}`}
              disabled={hitlSaving}
              onClick={() => void toggleYolo()}
            >
              <span className="hitl-switch-knob" />
            </button>
          </SettingsField>
        )}
      </SettingsSection>

      <SettingsSection
        title="Sons"
        icon="🔔"
        description="Toques discretos para resposta final, notificações, popups e passos do agente. Os sons são sintetizados localmente — nenhum áudio é baixado."
      >
        <SettingsField
          label="Modo silencioso"
          hint="Quando ativado, todos os efeitos sonoros são suprimidos."
          help={{
            title: "Modo silencioso",
            body: (
              <>
                Desliga todos os sons da interface: chime de resposta final,
                aviso de notificação, alerta de popup, tique-taque da contagem
                regressiva, lembrete de atenção e o tom grave dos passos do
                agente. A preferência é guardada no navegador (localStorage)
                e respeitada por todas as abas abertas.
              </>
            ),
          }}
          layout="row"
        >
          <button
            type="button"
            role="switch"
            aria-checked={soundMuted}
            className={`hitl-switch ${soundMuted ? "on" : "off"}`}
            onClick={() => setSoundMuted(!soundMuted)}
          >
            <span className="hitl-switch-knob" />
          </button>
        </SettingsField>
        {SOUND_KEYS.map((key) => {
          const value = soundVolumes[key];
          const tones = soundToneCount(key);
          return (
            <SettingsField
              key={key}
              label={SOUND_LABELS[key]}
              hint={`${tones === 2 ? "2 tons" : "1 tom"} • ${Math.round(value * 100)}%`}
              layout="row"
            >
              <div className="sound-row">
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={Math.round(value * 100)}
                  disabled={soundMuted}
                  onChange={(e) => setSoundVolume(key, Number(e.target.value) / 100)}
                  className="sound-slider"
                  aria-label={`Volume de ${SOUND_LABELS[key]}`}
                />
                <button
                  type="button"
                  className="settings-btn"
                  disabled={soundMuted}
                  onClick={() => sounds[key]()}
                  title="Tocar este som"
                >
                  Tocar
                </button>
              </div>
            </SettingsField>
          );
        })}
      </SettingsSection>
    </>
  );
}

