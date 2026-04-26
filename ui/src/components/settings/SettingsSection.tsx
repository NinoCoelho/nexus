import { useState, type ReactNode } from "react";
import HelpPopover from "./HelpPopover";

interface Props {
  title: string;
  icon?: ReactNode;
  description?: ReactNode;
  help?: { title: string; body: ReactNode };
  collapsible?: boolean;
  defaultOpen?: boolean;
  children: ReactNode;
}

export default function SettingsSection({
  title,
  icon,
  description,
  help,
  collapsible = false,
  defaultOpen = true,
  children,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const isOpen = !collapsible || open;

  const headerProps = collapsible
    ? {
        onClick: () => setOpen((v) => !v),
        role: "button" as const,
        "aria-expanded": isOpen,
      }
    : {};

  return (
    <section
      className={[
        "s-section",
        collapsible ? "s-section--collapsible" : "",
        collapsible && isOpen ? "s-section--open" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="s-section__header" {...headerProps}>
        {collapsible && <span className="s-section__caret">▶</span>}
        {icon && <span className="s-section__icon">{icon}</span>}
        <span className="s-section__title">{title}</span>
        {help && (
          <span onClick={(e) => e.stopPropagation()}>
            <HelpPopover title={help.title} body={help.body} />
          </span>
        )}
      </div>
      {isOpen && (
        <div className="s-section__body">
          {description && <p className="s-section__desc">{description}</p>}
          {children}
        </div>
      )}
    </section>
  );
}
