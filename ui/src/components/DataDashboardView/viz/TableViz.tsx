import type { VizProps } from "./types";
import { formatValue } from "./palette";

export default function TableViz({ columns, rows, config }: VizProps) {
  if (!rows.length || !columns.length) {
    return (
      <div
        style={{
          height: 100,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--fg-dim)",
          fontSize: 13,
        }}
      >
        No data to display
      </div>
    );
  }

  const numFormat = config.number_format as string | undefined;

  return (
    <div
      style={{
        maxHeight: 320,
        overflow: "auto",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border-soft)",
      }}
    >
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 12,
          color: "var(--fg)",
        }}
      >
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.name}
                style={{
                  position: "sticky",
                  top: 0,
                  background: "var(--bg-hover)",
                  padding: "6px 10px",
                  textAlign: "left",
                  fontWeight: 600,
                  fontSize: 11,
                  color: "var(--fg-dim)",
                  borderBottom: "1px solid var(--border)",
                  whiteSpace: "nowrap",
                }}
              >
                {col.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr
              key={ri}
              style={{
                background: ri % 2 === 0 ? "transparent" : "color-mix(in srgb, var(--bg-hover) 40%, transparent)",
              }}
            >
              {columns.map((col) => {
                const raw = row[col.name];
                const isNum = col.type === "number" || col.type === "integer" || col.type === "float";
                return (
                  <td
                    key={col.name}
                    style={{
                      padding: "5px 10px",
                      textAlign: isNum ? "right" : "left",
                      whiteSpace: "nowrap",
                      maxWidth: 200,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {isNum ? formatValue(raw, numFormat) : raw != null ? String(raw) : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
