import type { BoardFilters } from "./utils";

interface Props {
  filters: BoardFilters;
  onFiltersChange: (f: BoardFilters) => void;
}

export default function KanbanFilterBar({ filters, onFiltersChange }: Props) {
  const hasFilters = !!(filters.text || filters.label || filters.priority || filters.assignee);
  return (
    <div className="kanban-filter-bar">
      <input
        className="kanban-filter-input"
        type="search"
        placeholder="Search…"
        value={filters.text}
        onChange={(e) => onFiltersChange({ ...filters, text: e.target.value })}
      />
      <input
        className="kanban-filter-input"
        placeholder="Label"
        value={filters.label}
        onChange={(e) => onFiltersChange({ ...filters, label: e.target.value })}
      />
      <input
        className="kanban-filter-input"
        placeholder="Assignee"
        value={filters.assignee}
        onChange={(e) => onFiltersChange({ ...filters, assignee: e.target.value })}
      />
      <select
        className="kanban-filter-input"
        value={filters.priority}
        onChange={(e) => onFiltersChange({ ...filters, priority: e.target.value })}
      >
        <option value="">Any priority</option>
        <option value="urgent">Urgent</option>
        <option value="high">High</option>
        <option value="med">Medium</option>
        <option value="low">Low</option>
      </select>
      {hasFilters && (
        <button
          className="kanban-pill"
          onClick={() => onFiltersChange({ text: "", label: "", priority: "", assignee: "" })}
        >
          Clear
        </button>
      )}
    </div>
  );
}
