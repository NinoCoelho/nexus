/**
 * CoordinatorSection — Settings → Features: the master-chat coordinator.
 * Enable/disable, sweep cadence, quiet hours. Enable takes effect
 * immediately (PATCH /config wires the service live + provisions the
 * "Master" session).
 */

import { useCallback, useEffect, useState } from "react";
import {
  getConfig,
  patchConfig,
  type CoordinatorConfig,
} from "../../api/config";
import { useToast } from "../../toast/ToastProvider";
import SettingsField from "./SettingsField";
import SettingsSection from "./SettingsSection";

const DEFAULT_CFG: CoordinatorConfig = {
  enabled: false,
  session_id: "",
  name: "Master",
  persona: "",
  sweep_interval_minutes: 120,
  quiet_hours: "",
  auto_approve: [],
};

export default function CoordinatorSection() {
  const toast = useToast();
  const [cfg, setCfg] = useState<CoordinatorConfig>(DEFAULT_CFG);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [quietDraft, setQuietDraft] = useState("");
  const [intervalDraft, setIntervalDraft] = useState("120");
  const [nameDraft, setNameDraft] = useState("Master");
  const [personaDraft, setPersonaDraft] = useState("");

  const load = useCallback(async () => {
    try {
      const c = await getConfig();
      if (c.coordinator) {
        setCfg({ ...DEFAULT_CFG, ...c.coordinator });
        setQuietDraft(c.coordinator.quiet_hours ?? "");
        setIntervalDraft(String(c.coordinator.sweep_interval_minutes ?? 120));
        setNameDraft(c.coordinator.name || "Master");
        setPersonaDraft(c.coordinator.persona ?? "");
      }
    } catch {
      // Section renders defaults; save will surface errors.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (patch: Partial<CoordinatorConfig>) => {
      setSaving(true);
      try {
        const updated = await patchConfig({ coordinator: patch });
        if (updated.coordinator) setCfg({ ...DEFAULT_CFG, ...updated.coordinator });
      } catch (e) {
        toast.error("Couldn't save coordinator settings", {
          detail: e instanceof Error ? e.message : undefined,
        });
      } finally {
        setSaving(false);
      }
    },
    [toast],
  );

  const toggleEnabled = async () => {
    const next = !cfg.enabled;
    setCfg((c) => ({ ...c, enabled: next }));
    await save({ enabled: next });
    toast.success(
      next ? "Coordinator enabled — your Telegram DM becomes the master chat" : "Coordinator disabled",
    );
  };

  const commitInterval = async () => {
    const v = Math.max(0, Math.min(1440, parseInt(intervalDraft, 10) || 0));
    setIntervalDraft(String(v));
    if (v !== cfg.sweep_interval_minutes) await save({ sweep_interval_minutes: v });
  };

  const commitQuiet = async () => {
    const v = quietDraft.trim();
    if (v && !/^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$/.test(v)) {
      toast.error("Quiet hours must look like 23:00-08:00");
      return;
    }
    if (v !== cfg.quiet_hours) await save({ quiet_hours: v });
  };

  return (
    <SettingsSection
      title="Coordinator"
      icon="🎯"
      description="One master chat that sees every project and session, can delegate into any chat, and sweeps proactively — reached from your Telegram DM."
    >
      {loading && <p className="s-field__hint">Loading…</p>}
      <SettingsField
        label="Master chat"
        hint={
          cfg.enabled
            ? cfg.session_id
              ? "Enabled — Master session provisioned."
              : "Enabled — the Master session is created on the next server sync."
            : "Disabled."
        }
        help={{
          title: "Coordinator (master chat)",
          body: (
            <>
              When enabled, one designated <b>Master</b> session gets
              <code> nexus_sessions</code> (inspect projects/chats) and{" "}
              <code>session_dispatch</code> (run turns in other chats). Your
              Telegram DM binds to it instead of creating a throwaway chat, and
              periodic <b>read-only sweeps</b> digest activity and message you
              when something needs attention.
            </>
          ),
        }}
        layout="row"
      >
        <button
          type="button"
          role="switch"
          aria-checked={cfg.enabled}
          className={`hitl-switch ${cfg.enabled ? "on" : "off"}`}
          disabled={saving}
          onClick={() => void toggleEnabled()}
        >
          <span className="hitl-switch-knob" />
        </button>
      </SettingsField>

      {cfg.enabled && (
        <>
          <SettingsField
            label="Name"
            hint="The chat's title and badge (e.g. Jarvis, Alfred)."
            layout="row"
          >
            <input
              className="settings-input"
              type="text"
              value={nameDraft}
              maxLength={40}
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={() => {
                const v = nameDraft.trim() || "Master";
                setNameDraft(v);
                if (v !== cfg.name) void save({ name: v });
              }}
              style={{ width: 160 }}
            />
          </SettingsField>
          <SettingsField
            label="Persona"
            hint="How it behaves: tone, how it addresses you, focus areas. Fed verbatim to its prompt."
            help={{
              title: "Persona",
              body: (
                <>
                  Free-text personality for the master chat — e.g. "Address me
                  as Sir, dry British wit, be concise, proactively flag stale
                  projects". Kept in <code>[coordinator].persona</code> and
                  injected into every coordinator turn.
                </>
              ),
            }}
          >
            <textarea
              className="settings-input"
              rows={4}
              value={personaDraft}
              onChange={(e) => setPersonaDraft(e.target.value)}
              onBlur={() => {
                const v = personaDraft.trim();
                setPersonaDraft(v);
                if (v !== cfg.persona) void save({ persona: v });
              }}
              style={{ width: "100%", resize: "vertical" }}
            />
          </SettingsField>
          <SettingsField
            label="Sweep interval (minutes)"
            hint="How often the read-only digest runs. 0 disables sweeps."
            layout="row"
          >
            <input
              className="settings-input"
              type="number"
              min={0}
              max={1440}
              step={15}
              value={intervalDraft}
              onChange={(e) => setIntervalDraft(e.target.value)}
              onBlur={() => void commitInterval()}
              style={{ width: 110 }}
            />
          </SettingsField>
          <SettingsField
            label="Quiet hours"
            hint="Local-time window during which sweeps stay silent, e.g. 23:00-08:00."
            layout="row"
          >
            <input
              className="settings-input"
              type="text"
              placeholder="23:00-08:00"
              value={quietDraft}
              onChange={(e) => setQuietDraft(e.target.value)}
              onBlur={() => void commitQuiet()}
              style={{ width: 140 }}
            />
          </SettingsField>
        </>
      )}
    </SettingsSection>
  );
}
