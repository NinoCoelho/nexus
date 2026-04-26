/**
 * iCal RRULE helpers for the Repeat picker.
 *
 * The UI deals in named *presets* (Never, Every Day, Every Weekday, etc).
 * Each preset round-trips to a canonical RRULE string the backend already
 * understands. The "Custom" preset is backed by a small editable record
 * (frequency + interval + day-of-week list).
 *
 * RRULEs the picker can't decode lossly fall back to "custom" with default
 * values; the user gets a fresh editor and the original RRULE is overwritten
 * on save. Power users can edit the markdown directly for cases beyond
 * FREQ/INTERVAL/BYDAY.
 */

export type RepeatPreset =
  | "never"
  | "daily"
  | "weekday"
  | "weekly"
  | "biweekly"
  | "monthly"
  | "yearly"
  | "custom";

export type RepeatFrequency = "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY";

export interface CustomRepeat {
  freq: RepeatFrequency;
  interval: number;
  byday: string[]; // ["MO","WE","FR"]; only meaningful for WEEKLY
}

export interface PresetState {
  preset: RepeatPreset;
  custom: CustomRepeat;
}

export const REPEAT_PRESETS: { value: RepeatPreset; label: string }[] = [
  { value: "never", label: "Never" },
  { value: "daily", label: "Every Day" },
  { value: "weekday", label: "Every Weekday" },
  { value: "weekly", label: "Every Week" },
  { value: "biweekly", label: "Every 2 Weeks" },
  { value: "monthly", label: "Every Month" },
  { value: "yearly", label: "Every Year" },
  { value: "custom", label: "Custom…" },
];

/** Sun-first labels (matches macOS Calendar). */
export const WEEKDAYS: { code: string; label: string }[] = [
  { code: "SU", label: "S" },
  { code: "MO", label: "M" },
  { code: "TU", label: "T" },
  { code: "WE", label: "W" },
  { code: "TH", label: "T" },
  { code: "FR", label: "F" },
  { code: "SA", label: "S" },
];

const DEFAULT_CUSTOM: CustomRepeat = { freq: "WEEKLY", interval: 1, byday: [] };

export function presetToRRule(preset: RepeatPreset, custom?: CustomRepeat): string {
  switch (preset) {
    case "never":
      return "";
    case "daily":
      return "FREQ=DAILY";
    case "weekday":
      return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR";
    case "weekly":
      return "FREQ=WEEKLY";
    case "biweekly":
      return "FREQ=WEEKLY;INTERVAL=2";
    case "monthly":
      return "FREQ=MONTHLY";
    case "yearly":
      return "FREQ=YEARLY";
    case "custom": {
      if (!custom) return "";
      const parts = [`FREQ=${custom.freq}`];
      if (custom.interval > 1) parts.push(`INTERVAL=${custom.interval}`);
      if (custom.freq === "WEEKLY" && custom.byday.length > 0) {
        parts.push(`BYDAY=${custom.byday.join(",")}`);
      }
      return parts.join(";");
    }
  }
}

export function rruleToPreset(rrule: string | null | undefined): PresetState {
  const norm = (rrule ?? "").trim().toUpperCase();
  if (!norm) return { preset: "never", custom: { ...DEFAULT_CUSTOM } };
  if (norm === "FREQ=DAILY") return { preset: "daily", custom: { ...DEFAULT_CUSTOM } };
  if (norm === "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") {
    return { preset: "weekday", custom: { ...DEFAULT_CUSTOM, byday: ["MO","TU","WE","TH","FR"] } };
  }
  if (norm === "FREQ=WEEKLY") return { preset: "weekly", custom: { ...DEFAULT_CUSTOM } };
  if (norm === "FREQ=WEEKLY;INTERVAL=2") {
    return { preset: "biweekly", custom: { freq: "WEEKLY", interval: 2, byday: [] } };
  }
  if (norm === "FREQ=MONTHLY") return { preset: "monthly", custom: { freq: "MONTHLY", interval: 1, byday: [] } };
  if (norm === "FREQ=YEARLY") return { preset: "yearly", custom: { freq: "YEARLY", interval: 1, byday: [] } };

  // Anything else -> custom (best-effort parse).
  const parts: Record<string, string> = {};
  for (const seg of norm.split(";")) {
    const [k, v] = seg.split("=");
    if (k && v !== undefined) parts[k] = v;
  }
  const freqRaw = parts.FREQ as RepeatFrequency | undefined;
  const freq: RepeatFrequency =
    freqRaw && (["DAILY","WEEKLY","MONTHLY","YEARLY"] as const).includes(freqRaw as RepeatFrequency)
      ? (freqRaw as RepeatFrequency)
      : "WEEKLY";
  const interval = parts.INTERVAL ? Math.max(1, parseInt(parts.INTERVAL, 10) || 1) : 1;
  const byday = parts.BYDAY ? parts.BYDAY.split(",").filter(Boolean) : [];
  return { preset: "custom", custom: { freq, interval, byday } };
}

export const FREQ_NOUN: Record<RepeatFrequency, string> = {
  DAILY: "day",
  WEEKLY: "week",
  MONTHLY: "month",
  YEARLY: "year",
};
