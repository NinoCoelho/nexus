import "./BrandMark.css";

type Size = "sm" | "lg";

export function BrandMark({ size = "sm" }: { size?: Size }) {
  return (
    <span className={`brand-mark brand-mark--${size}`} aria-label="Nexus">
      <NexusIcon />
      <span className="brand-mark__text">NEXUS</span>
    </span>
  );
}

function NexusIcon() {
  // Atom: central node + three orbital ellipses. Uses currentColor so it
  // inherits whatever text color the surrounding theme provides.
  return (
    <svg
      className="brand-mark__icon"
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <ellipse cx="16" cy="16" rx="14" ry="5.5" />
      <ellipse cx="16" cy="16" rx="14" ry="5.5" transform="rotate(60 16 16)" />
      <ellipse cx="16" cy="16" rx="14" ry="5.5" transform="rotate(120 16 16)" />
      <circle cx="16" cy="16" r="3.6" fill="currentColor" stroke="none" />
      <circle cx="16" cy="2.6" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}
