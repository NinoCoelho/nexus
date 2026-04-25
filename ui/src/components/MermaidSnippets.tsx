import { useEffect, useRef, useState } from "react";

const SNIPPETS: { label: string; body: string }[] = [
  {
    label: "flowchart",
    body: "```mermaid\nflowchart TD\n    A[Start] --> B{Decision}\n    B -- Yes --> C[Do thing]\n    B -- No  --> D[Skip]\n    C --> E[End]\n    D --> E\n```",
  },
  {
    label: "sequence",
    body: "```mermaid\nsequenceDiagram\n    participant U as User\n    participant S as Server\n    U->>S: Request\n    S-->>U: Response\n```",
  },
  {
    label: "class",
    body: "```mermaid\nclassDiagram\n    class Animal {\n      +String name\n      +eat()\n    }\n    class Dog\n    Animal <|-- Dog\n```",
  },
  {
    label: "state",
    body: "```mermaid\nstateDiagram-v2\n    [*] --> Idle\n    Idle --> Running: start\n    Running --> Idle: stop\n    Running --> [*]: done\n```",
  },
  {
    label: "mindmap",
    body: "```mermaid\nmindmap\n  root((idea))\n    branch one\n      sub one\n      sub two\n    branch two\n```",
  },
  {
    label: "erDiagram",
    body: "```mermaid\nerDiagram\n    USER ||--o{ POST : writes\n    POST }o--|| TAG : has\n```",
  },
];

interface Props {
  onInsert: (body: string) => void;
}

export default function MermaidSnippets({ onInsert }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="vault-mermaid-snippets" ref={ref}>
      <button
        type="button"
        className="vault-pill"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Insert a mermaid diagram template"
      >
        Mermaid ▾
      </button>
      {open && (
        <div className="vault-mermaid-menu" role="menu">
          {SNIPPETS.map((s) => (
            <button
              key={s.label}
              type="button"
              role="menuitem"
              className="vault-mermaid-menu-item"
              onClick={() => { onInsert(`\n${s.body}\n`); setOpen(false); }}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
