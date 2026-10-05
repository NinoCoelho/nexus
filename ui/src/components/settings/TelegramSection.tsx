import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Send } from "lucide-react";
import {
  getConfig,
  patchConfig,
  type TelegramConfig,
} from "../../api/config";
import {
  getTelegramStatus,
  startTelegram,
  stopTelegram,
  type TelegramStatus,
} from "../../api/telegram";
import TelegramBindingsPanel from "./TelegramBindingsPanel";
import { setCredential } from "../../api/credentials";
import { useToast } from "../../toast/ToastProvider";
import SettingsSection from "./SettingsSection";

const DEFAULT_CFG: TelegramConfig = {
  enabled: false,
  bot_token_env: "TELEGRAM_BOT_TOKEN",
  allowed_user_ids: [],
  poll_timeout_seconds: 25,
  stream_edits: true,
  proxy_url: "",
  deny_message: true,
  ack_reaction: "👀",
  voice_replies: true,
  voice_speechify: "auto",
  web_sync: true,
};

/**
 * Telegram bot — long-polling gateway into Nexus chats. The [telegram]
 * config values are patched into config.toml; the bot token is stored via
 * the credentials store (never round-trips) and the poller can be started
 * / stopped live without a server restart.
 */
export default function TelegramSection() {
  const { t } = useTranslation("settings");
  const toast = useToast();
  const [cfg, setCfg] = useState<TelegramConfig>(DEFAULT_CFG);
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tokenDraft, setTokenDraft] = useState("");
  const [allowlistDraft, setAllowlistDraft] = useState<string>("");

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await getTelegramStatus());
    } catch {
      // Status is cosmetic; the section still works without it.
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getConfig();
      if (c.telegram) {
        setCfg({ ...DEFAULT_CFG, ...c.telegram });
        setAllowlistDraft((c.telegram.allowed_user_ids ?? []).join(", "));
      }
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load Telegram config");
    } finally {
      setLoading(false);
    }
  }, [refreshStatus]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (patch: Partial<Omit<TelegramConfig, "has_token">>) => {
      setSaving(true);
      setError(null);
      try {
        const updated = await patchConfig({ telegram: patch });
        if (updated.telegram) setCfg({ ...DEFAULT_CFG, ...updated.telegram });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to save");
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const handleToggleEnabled = async () => {
    const next = !cfg.enabled;
    setCfg((c) => ({ ...c, enabled: next }));
    await save({ enabled: next });
    if (next && !status?.running) {
      // Best-effort live start so the toggle alone makes the bot work.
      if (status?.has_token || tokenDraft.trim()) {
        await handleStart();
      }
    }
  };

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    try {
      const s = await startTelegram();
      setStatus(s);
      toast.success(
        t("settings:telegram.toast.started", {
          bot: s.bot_username ? `@${s.bot_username}` : "bot",
        }),
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : "start failed";
      setError(msg);
      toast.error(t("settings:telegram.toast.startFailed"), { detail: msg });
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    setStarting(true);
    setError(null);
    try {
      setStatus(await stopTelegram());
      toast.info(t("settings:telegram.toast.stopped"));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "stop failed";
      setError(msg);
    } finally {
      setStarting(false);
    }
  };

  const handleSaveToken = async () => {
    const value = tokenDraft.trim();
    if (!value) return;
    setSaving(true);
    setError(null);
    try {
      await setCredential(cfg.bot_token_env, value, { kind: "generic" });
      setTokenDraft("");
      const c = await getConfig();
      if (c.telegram) setCfg((prev) => ({ ...prev, ...c.telegram }));
      await refreshStatus();
      toast.success(t("settings:telegram.toast.tokenSaved"));
      // The running poller still holds the OLD token (and Telegram may
      // have revoked it) — restart it so the new one takes effect now.
      if (cfg.enabled) {
        await handleStart();
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to save token";
      setError(msg);
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAllowlist = async () => {
    const ids = allowlistDraft
      .split(/[\s,;]+/)
      .filter(Boolean)
      .map((s) => Number(s));
    if (ids.some((n) => !Number.isInteger(n) || n <= 0)) {
      setError(t("settings:telegram.allowlistInvalid"));
      return;
    }
    setError(null);
    await save({ allowed_user_ids: ids });
    toast.success(t("settings:telegram.toast.allowlistSaved"));
  };

  const running = status?.running ?? false;
  const hasToken = status?.has_token ?? cfg.has_token ?? false;

  return (
    <SettingsSection
      title={t("settings:telegram.sectionTitle")}
      icon={<Send size={16} />}
      description={t("settings:telegram.sectionDescription")}
      collapsible
      defaultOpen={false}
      help={{
        title: t("settings:telegram.helpTitle"),
        body: (
          <>
            {t("settings:telegram.helpBody1")}{" "}
            <b>@BotFather</b> {t("settings:telegram.helpBody2")}{" "}
            <b>{t("settings:telegram.helpBody3")}</b>{" "}
            {t("settings:telegram.helpBody4")}
          </>
        ),
      }}
    >
      <div className="settings-row">
        <span className="settings-row-name">
          {t("settings:telegram.enabledLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {running
              ? t("settings:telegram.statusRunning", {
                  bot: status?.bot_username ? `@${status.bot_username}` : "bot",
                })
              : cfg.enabled
                ? t("settings:telegram.statusStopped")
                : t("settings:telegram.statusDisabled")}
          </span>
        </span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {cfg.enabled &&
            (running ? (
              <button
                type="button"
                className="settings-btn"
                onClick={handleStop}
                disabled={starting}
              >
                {t("settings:telegram.stopButton")}
              </button>
            ) : (
              <button
                type="button"
                className="settings-btn settings-btn--primary"
                onClick={handleStart}
                disabled={starting || (!hasToken && !tokenDraft.trim())}
                title={
                  !hasToken && !tokenDraft.trim()
                    ? t("settings:telegram.startNeedsToken")
                    : undefined
                }
              >
                {starting ? t("settings:telegram.starting") : t("settings:telegram.startButton")}
              </button>
            ))}
          <button
            className={`hitl-switch${cfg.enabled ? " on" : ""}`}
            onClick={handleToggleEnabled}
            aria-pressed={cfg.enabled}
            disabled={saving}
          >
            <span className="hitl-switch-knob" />
          </button>
        </div>
      </div>

      <div className="settings-row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="settings-row-name">
          {t("settings:telegram.tokenLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {hasToken
              ? t("settings:telegram.tokenPresent")
              : t("settings:telegram.tokenMissing")}
          </span>
        </span>
        <div
          style={{ display: "flex", gap: 8, flexBasis: "100%", marginTop: 4 }}
        >
          <input
            className="settings-input"
            type="password"
            autoComplete="off"
            placeholder={hasToken ? t("settings:telegram.tokenReplaceHint") : "123456:ABC-…"}
            value={tokenDraft}
            onChange={(e) => setTokenDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSaveToken();
            }}
            style={{ flex: 1 }}
          />
          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveToken}
            disabled={saving || !tokenDraft.trim()}
          >
            {t("settings:telegram.tokenSave")}
          </button>
        </div>
      </div>

      <div className="settings-row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="settings-row-name">
          {t("settings:telegram.allowlistLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.allowlistHint")}
          </span>
        </span>
        <div
          style={{ display: "flex", gap: 8, flexBasis: "100%", marginTop: 4 }}
        >
          <input
            className="settings-input"
            inputMode="numeric"
            placeholder="12345678, 87654321"
            value={allowlistDraft}
            onChange={(e) => setAllowlistDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSaveAllowlist();
            }}
            style={{ flex: 1 }}
          />
          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveAllowlist}
            disabled={saving}
          >
            {t("settings:telegram.allowlistSave")}
          </button>
        </div>
      </div>

      <div className="settings-row">
        <span className="settings-row-name">
          {t("settings:telegram.streamEditsLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.streamEditsHint")}
          </span>
        </span>
        <button
          className={`hitl-switch${cfg.stream_edits ? " on" : ""}`}
          onClick={() => void save({ stream_edits: !cfg.stream_edits })}
          aria-pressed={cfg.stream_edits}
          disabled={saving}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>

      <div className="settings-row">
        <span className="settings-row-name">
          {t("settings:telegram.voiceRepliesLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.voiceRepliesHint")}
          </span>
        </span>
        <button
          className={`hitl-switch${cfg.voice_replies ? " on" : ""}`}
          onClick={() => void save({ voice_replies: !cfg.voice_replies })}
          aria-pressed={cfg.voice_replies}
          disabled={saving}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>

      <div className="settings-row">
        <span className="settings-row-name">
          {t("settings:telegram.webSyncLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.webSyncHint")}
          </span>
        </span>
        <button
          className={`hitl-switch${cfg.web_sync ? " on" : ""}`}
          onClick={() => void save({ web_sync: !cfg.web_sync })}
          aria-pressed={cfg.web_sync}
          disabled={saving}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>

      <div className="settings-row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="settings-row-name">
          {t("settings:telegram.speechifyLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.speechifyHint")}
          </span>
        </span>
        <div style={{ flexBasis: "100%", marginTop: 4 }}>
          <select
            className="s-select"
            disabled={saving}
            value={cfg.voice_speechify}
            onChange={(e) => void save({ voice_speechify: e.target.value as TelegramConfig["voice_speechify"] })}
          >
            <option value="auto">{t("settings:telegram.speechifyAuto")}</option>
            <option value="always">{t("settings:telegram.speechifyAlways")}</option>
            <option value="off">{t("settings:telegram.speechifyOff")}</option>
          </select>
        </div>
      </div>

      <div className="settings-row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="settings-row-name">
          {t("settings:telegram.ackReactionLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.ackReactionHint")}
          </span>
        </span>
        <input
          className="settings-input"
          placeholder="👀"
          defaultValue={cfg.ack_reaction}
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (v !== cfg.ack_reaction) {
              void save({ ack_reaction: v });
            }
          }}
          style={{ width: 120, marginTop: 4 }}
        />
      </div>

      <div className="settings-row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="settings-row-name">
          {t("settings:telegram.proxyLabel")}
          <span
            className="settings-row-hint"
            style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}
          >
            {t("settings:telegram.proxyHint")}
          </span>
        </span>
        <input
          className="settings-input"
          placeholder="http://127.0.0.1:7890"
          defaultValue={cfg.proxy_url}
          onBlur={(e) => {
            if (e.target.value.trim() !== cfg.proxy_url) {
              void save({ proxy_url: e.target.value.trim() });
            }
          }}
          style={{ flexBasis: "100%", marginTop: 4 }}
        />
      </div>

      {cfg.enabled && (
        <div className="tg-bindings-wrap">
          <div className="s-field__hint" style={{ marginTop: 12 }}>
            Linked chats &amp; topics
          </div>
          <TelegramBindingsPanel />
        </div>
      )}
      {cfg.enabled && status?.error && !running && (
        <p className="settings-error">{status.error}</p>
      )}
      {cfg.enabled && status?.allowlist_empty && (
        <p className="settings-error">{t("settings:telegram.allowlistEmptyWarning")}</p>
      )}
      {loading && <p className="settings-info">Loading…</p>}
      {error && <p className="settings-error">{error}</p>}
    </SettingsSection>
  );
}
