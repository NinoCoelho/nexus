import type { ReactNode } from "react";
import HelpPopover from "./HelpPopover";

interface Props {
  label: string;
  hint?: ReactNode;
  help?: { title: string; body: ReactNode };
  error?: string | null;
  layout?: "stacked" | "row";
  children: ReactNode;
}

export default function SettingsField({
  label,
  hint,
  help,
  error,
  layout = "stacked",
  children,
}: Props) {
  return (
    <div className="s-field">
      {layout === "row" ? (
        <div className="s-field__row">
          <div className="s-field__label-row">
            <label className="s-field__label">{label}</label>
            {help && <HelpPopover title={help.title} body={help.body} />}
          </div>
          {children}
        </div>
      ) : (
        <>
          <div className="s-field__label-row">
            <label className="s-field__label">{label}</label>
            {help && <HelpPopover title={help.title} body={help.body} />}
          </div>
          {children}
        </>
      )}
      {hint && <p className="s-field__hint">{hint}</p>}
      {error && <p className="s-field__error">{error}</p>}
    </div>
  );
}
