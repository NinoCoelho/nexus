/**
 * Compact Nexus usage gauge for the left sidebar.
 *
 * Visual: a single thin progress bar with no inline text — the bar
 * silently summarises the user's spend-vs-budget. Hover surfaces full
 * details (tier, spend, period, reset countdown) via an absolute
 * tooltip. Click jumps to Settings → Nexus.
 *
 * Visible whenever the user is signed in to a Nexus account, regardless
 * of tier (free has its own $0.50/5h budget). Hidden entirely when
 * signed-out so the sidebar bottom stays clean.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNexusAccount } from "../../hooks/useNexusAccount";
import "./NexusUsageGauges.css";

interface Props {
  collapsed: boolean;
  onOpenSettings: () => void;
}

function formatMoney(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value >= 100) return `$${value.toFixed(0)}`;
  return `$${value.toFixed(2)}`;
}

function formatResetIn(iso: string | undefined | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const secs = Math.max(0, Math.round((t - Date.now()) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

function spendBucket(ratio: number): "ok" | "warn" | "high" {
  if (ratio >= 0.9) return "high";
  if (ratio >= 0.7) return "warn";
  return "ok";
}

export default function NexusUsageGauges({ collapsed, onOpenSettings }: Props) {
  const { t } = useTranslation("settings");
  const { status } = useNexusAccount();
  const [hovered, setHovered] = useState(false);

  if (!status?.signedIn || status.cancelsAt) return null;
  const live = status.status;
  if (!live || live.maxBudget <= 0) return null;

  const ratio = Math.min(1, Math.max(0, live.spend / live.maxBudget));
  const bucket = spendBucket(ratio);
  const tierLabel =
    status.tier === "pro"
      ? t("settings:nexus.account.tierPro")
      : t("settings:nexus.account.tierFree");
  const resetIn = formatResetIn(live.budgetResetAt);

  return (
    <button
      type="button"
      className={`nexus-gauges ${collapsed ? "nexus-gauges--collapsed" : ""}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={() => setHovered(false)}
      onClick={onOpenSettings}
      aria-label={t("settings:nexus.gauges.openSettings")}
    >
      <div className={`nexus-gauges-bar nexus-gauges-bar-${bucket}`}>
        <div
          className="nexus-gauges-fill"
          style={{ width: `${Math.round(ratio * 100)}%` }}
        />
      </div>
      {hovered && (
        <div role="tooltip" className="nexus-gauges-tooltip">
          <div className="nexus-gauges-tooltip-row nexus-gauges-tooltip-tier">
            {tierLabel}
          </div>
          <div className="nexus-gauges-tooltip-row">
            {formatMoney(live.spend)} / {formatMoney(live.maxBudget)}
          </div>
          {(live.budgetDuration || resetIn) && (
            <div className="nexus-gauges-tooltip-row nexus-gauges-tooltip-dim">
              {live.budgetDuration}
              {live.budgetDuration && resetIn ? " · " : ""}
              {resetIn ? `resets in ${resetIn}` : ""}
            </div>
          )}
        </div>
      )}
    </button>
  );
}
