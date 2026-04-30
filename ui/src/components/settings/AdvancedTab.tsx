import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("settings");
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
        toast.error(t("settings:advanced.toast.loadFailed"), {
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
      toast.success(t("settings:advanced.toast.saved"));
    } catch (e) {
      toast.error(t("settings:advanced.toast.saveFailed"), {
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
      toast.success(next ? t("settings:advanced.toast.yoloEnabled") : t("settings:advanced.toast.yoloDisabled"));
    } catch (e) {
      toast.error(t("settings:advanced.toast.saveFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setHitlSaving(false);
    }
  }

  return (
    <>
      <SettingsSection
        title={t("settings:advanced.agentBehaviorTitle")}
        icon={t("settings:advanced.agentBehaviorIcon")}
        description={t("settings:advanced.agentBehaviorDescription")}
      >
        {loading && <p className="s-field__hint">{t("settings:advanced.loading")}</p>}
        {agent && (
          <>
            <SettingsField
              label={t("settings:advanced.maxStepsLabel")}
              hint={t("settings:advanced.maxStepsHint")}
              help={{
                title: t("settings:advanced.maxStepsHelpTitle"),
                body: (
                  <>
                    Each agent turn can involve multiple round-trips: call a tool,
                    read the result, decide the next step. This limit (default{" "}
                    {AGENT_DEFAULTS.max_iterations}) keeps the agent from getting
                    stuck in a loop. Raise it if you use the agent for long tasks
                    (research, refactor); lower it to cap cost.
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
              label={t("settings:advanced.temperatureLabel")}
              hint={t("settings:advanced.temperatureHint")}
              help={{
                title: t("settings:advanced.temperatureHelpTitle"),
                body: (
                  <>
                    Controls model randomness. At <b>0</b>, the model always
                    picks the most likely token — ideal when the agent needs to
                    call tools with valid JSON arguments. Above 0.7, responses
                    get creative but JSON structure can break. Most users
                    should leave this at 0.
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
              label={t("settings:advanced.frequencyPenaltyLabel")}
              hint={t("settings:advanced.frequencyPenaltyHint")}
              help={{
                title: t("settings:advanced.frequencyPenaltyHelpTitle"),
                body: (
                  <>
                    Local models (deepseek-coder, some llama variants) can fall
                    into degeneration — repeating "@@@@…" forever. This
                    parameter penalizes already-used tokens and dramatically
                    reduces that problem. Default: {AGENT_DEFAULTS.frequency_penalty}.
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
              label={t("settings:advanced.presencePenaltyLabel")}
              hint={t("settings:advanced.presencePenaltyHint")}
              help={{
                title: t("settings:advanced.presencePenaltyHelpTitle"),
                body: (
                  <>
                    Unlike frequency penalty (which looks at how many times a
                    token appeared), this one penalizes any token that has
                    appeared at least once. Use positive values to encourage
                    new topics. Default: {AGENT_DEFAULTS.presence_penalty}.
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
              label={t("settings:advanced.antiRepeatLabel")}
              hint={t("settings:advanced.antiRepeatHint")}
              help={{
                title: t("settings:advanced.antiRepeatHelpTitle"),
                body: (
                  <>
                    Safeguard against models that loop by emitting the same
                    pattern forever. If the tail of the response is a pattern
                    of up to 8 characters repeated for <b>N</b> characters,
                    the stream is cut with finish_reason=stop. Default:{" "}
                    {AGENT_DEFAULTS.anti_repeat_threshold}. Set to 0 to
                    disable (not recommended).
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

            <SettingsField
              label={t("settings:advanced.maxOutputLabel")}
              hint={t("settings:advanced.maxOutputHint")}
              help={{
                title: t("settings:advanced.maxOutputHelpTitle"),
                body: (
                  <>
                    Forwarded to the LLM as <code>max_tokens</code>. Resolution
                    order is per-model override (set on a Model entry) → this
                    global default → provider fallback. Set to a higher value
                    (e.g. 16384, 32768) to let long-form models stretch out;
                    set lower to cap cost/latency. 0 means "don't pass the
                    field" — the OpenAI-compat path leaves it out entirely;
                    Anthropic uses its legacy 4096 because its API requires
                    the field.
                  </>
                ),
              }}
              layout="row"
            >
              <NumberFieldWithDefault
                value={agent.default_max_output_tokens ?? AGENT_DEFAULTS.default_max_output_tokens}
                defaultValue={AGENT_DEFAULTS.default_max_output_tokens}
                min={0}
                max={200000}
                step={1024}
                onCommit={(v) => void patchAgent({ default_max_output_tokens: v })}
              />
            </SettingsField>
          </>
        )}
      </SettingsSection>

      <SettingsSection
        title={t("settings:advanced.autoApprovalTitle")}
        icon={t("settings:advanced.autoApprovalIcon")}
        description={t("settings:advanced.autoApprovalDescription")}
      >
        {hitl && (
          <SettingsField
            label={t("settings:advanced.autoApproveLabel")}
            hint={t("settings:advanced.autoApproveHint")}
            help={{
              title: t("settings:advanced.autoApproveHelpTitle"),
              body: (
                <>
                  The agent asks before running potentially dangerous commands
                  (deleting a file, running a shell, etc.). Enabling this mode
                  means it <b>won't</b> wait for your confirmation on binary
                  questions. Only use it if you trust the agent and have
                  thoroughly reviewed its skills.
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
        title={t("settings:advanced.soundsTitle")}
        icon={t("settings:advanced.soundsIcon")}
        description={t("settings:advanced.soundsDescription")}
      >
        <SettingsField
          label={t("settings:advanced.silentModeLabel")}
          hint={t("settings:advanced.silentModeHint")}
          help={{
            title: t("settings:advanced.silentModeHelpTitle"),
            body: (
              <>
                Turns off every UI sound: final-response chime, notification
                alert, popup alert, countdown tick, attention reminder, and
                the low tone for agent steps. The preference is saved in the
                browser (localStorage) and respected across all open tabs.
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
              hint={`${t("settings:advanced.toneCount", { count: tones })} • ${Math.round(value * 100)}%`}
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
                  aria-label={`Volume for ${SOUND_LABELS[key]}`}
                />
                <button
                  type="button"
                  className="settings-btn"
                  disabled={soundMuted}
                  onClick={() => sounds[key]()}
                  title={t("settings:advanced.playTitle")}
                >
                  {t("settings:advanced.playButton")}
                </button>
              </div>
            </SettingsField>
          );
        })}
      </SettingsSection>
    </>
  );
}

