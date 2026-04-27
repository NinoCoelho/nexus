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
        toast.error("Failed to load advanced settings", {
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
      toast.success("Settings saved");
    } catch (e) {
      toast.error("Failed to save", {
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
      toast.success(next ? "Auto-approval enabled" : "Auto-approval disabled");
    } catch (e) {
      toast.error("Failed to save", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setHitlSaving(false);
    }
  }

  return (
    <>
      <SettingsSection
        title="Agent behavior"
        icon="⚙"
        description="Generation fine-tuning. The defaults work well in most cases."
      >
        {loading && <p className="s-field__hint">Loading…</p>}
        {agent && (
          <>
            <SettingsField
              label="Max steps per turn"
              hint="Cap on tool-loop iterations before the agent stops. High values allow complex tasks; low values guard against loops."
              help={{
                title: "Max steps per turn",
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
              label="Temperature"
              hint="0 = deterministic (recommended for tool-calling). Higher values raise creativity at the cost of unstable output."
              help={{
                title: "Temperature",
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
              label="Frequency penalty"
              hint="Lowers the chance of already-used tokens reappearing. Useful against local models that get stuck in loops."
              help={{
                title: "Frequency penalty",
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
              label="Presence penalty"
              hint="Nudges the model toward new topics. Default 0 (no effect)."
              help={{
                title: "Presence penalty",
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
              label="Infinite-loop detector"
              hint="Aborts the stream if the output keeps repeating the same pattern for N characters. 0 disables."
              help={{
                title: "Infinite-loop detector",
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
          </>
        )}
      </SettingsSection>

      <SettingsSection
        title="Auto-approval"
        icon="🤖"
        description="When a dangerous tool asks for confirmation before running, the agent normally opens a dialog. With auto-approval enabled, yes/no questions are answered automatically."
      >
        {hitl && (
          <SettingsField
            label="Auto-approve yes/no questions"
            hint="Doesn't affect open-ended text or multiple-choice questions — you'll still need to answer those."
            help={{
              title: "Auto-approval (YOLO)",
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
        title="Sounds"
        icon="🔔"
        description="Subtle tones for final response, notifications, popups and agent steps. Sounds are synthesized locally — no audio is downloaded."
      >
        <SettingsField
          label="Silent mode"
          hint="When on, all sound effects are suppressed."
          help={{
            title: "Silent mode",
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
              hint={`${tones === 2 ? "2 tones" : "1 tone"} • ${Math.round(value * 100)}%`}
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
                  title="Play this sound"
                >
                  Play
                </button>
              </div>
            </SettingsField>
          );
        })}
      </SettingsSection>
    </>
  );
}

