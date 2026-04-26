/**
 * RepeatPicker — friendly preset dropdown for RRULE recurrence.
 *
 * Selecting "Custom…" opens a small sub-dialog with frequency/interval/day-of-week
 * controls, matching macOS Calendar's pattern. Emits the canonical RRULE string
 * via onChange so the parent stays oblivious to the picker's internal state.
 */

import { useEffect, useState } from "react";
import {
  type CustomRepeat,
  type RepeatFrequency,
  type RepeatPreset,
  FREQ_NOUN,
  REPEAT_PRESETS,
  WEEKDAYS,
  presetToRRule,
  rruleToPreset,
} from "./rrule";

interface Props {
  value: string;
  onChange: (rrule: string) => void;
}

export default function RepeatPicker({ value, onChange }: Props) {
  const initial = rruleToPreset(value);
  const [preset, setPreset] = useState<RepeatPreset>(initial.preset);
  const [custom, setCustom] = useState<CustomRepeat>(initial.custom);
  const [customOpen, setCustomOpen] = useState(false);

  // Re-sync if the underlying RRULE changes externally (e.g. agent edit).
  useEffect(() => {
    const next = rruleToPreset(value);
    setPreset(next.preset);
    setCustom(next.custom);
  }, [value]);

  function handlePresetChange(p: RepeatPreset) {
    setPreset(p);
    if (p === "custom") {
      setCustomOpen(true);
    } else {
      onChange(presetToRRule(p));
    }
  }

  function handleCustomSave() {
    onChange(presetToRRule("custom", custom));
    setCustomOpen(false);
  }

  function toggleDay(code: string) {
    setCustom((c) => {
      const has = c.byday.includes(code);
      return { ...c, byday: has ? c.byday.filter((d) => d !== code) : [...c.byday, code] };
    });
  }

  return (
    <>
      <select value={preset} onChange={(e) => handlePresetChange(e.target.value as RepeatPreset)}>
        {REPEAT_PRESETS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {customOpen && (
        <div className="cal-modal-backdrop" onClick={() => setCustomOpen(false)}>
          <div className="cal-modal cal-modal--small" onClick={(e) => e.stopPropagation()}>
            <h3>Custom repeat</h3>

            <label>
              Frequency
              <select
                value={custom.freq}
                onChange={(e) => setCustom({ ...custom, freq: e.target.value as RepeatFrequency })}
              >
                <option value="DAILY">Daily</option>
                <option value="WEEKLY">Weekly</option>
                <option value="MONTHLY">Monthly</option>
                <option value="YEARLY">Yearly</option>
              </select>
            </label>

            <label>
              Every
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="number"
                  min={1}
                  max={99}
                  value={custom.interval}
                  onChange={(e) =>
                    setCustom({
                      ...custom,
                      interval: Math.max(1, Math.min(99, parseInt(e.target.value, 10) || 1)),
                    })
                  }
                  style={{ width: 64 }}
                />
                <span style={{ fontSize: 13 }}>
                  {FREQ_NOUN[custom.freq]}{custom.interval === 1 ? "" : "s"}
                </span>
              </div>
            </label>

            {custom.freq === "WEEKLY" && (
              <label>
                On
                <div className="cal-day-picker">
                  {WEEKDAYS.map((d, i) => {
                    const on = custom.byday.includes(d.code);
                    return (
                      <button
                        key={`${d.code}-${i}`}
                        type="button"
                        className={on ? "active" : ""}
                        onClick={() => toggleDay(d.code)}
                        aria-pressed={on}
                      >
                        {d.label}
                      </button>
                    );
                  })}
                </div>
              </label>
            )}

            <div className="cal-modal-actions">
              <div className="spacer" />
              <button onClick={() => setCustomOpen(false)}>Cancel</button>
              <button className="primary" onClick={handleCustomSave}>OK</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
