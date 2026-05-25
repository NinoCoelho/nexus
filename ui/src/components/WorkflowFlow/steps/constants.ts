import type { StepType, TriggerType } from "../../../types/workflow";

export const STEP_TYPES: { value: StepType; label: string }[] = [
  { value: "tool_call", label: "Tool Call" },
  { value: "agent_session", label: "Agent Session" },
  { value: "transform", label: "Transform" },
  { value: "delay", label: "Delay" },
  { value: "http_request", label: "HTTP Request" },
  { value: "mcp_call", label: "MCP Call" },
  { value: "kanban_action", label: "Kanban Action" },
  { value: "table_action", label: "App Table Action" },
  { value: "return_step", label: "Return" },
];

export const TRIGGER_TYPES: { value: TriggerType; label: string }[] = [
  { value: "manual", label: "Manual" },
  { value: "webhook", label: "Webhook" },
  { value: "schedule", label: "Schedule" },
  { value: "fs_watch", label: "File Watch" },
  { value: "event", label: "Event" },
];

export const STEP_ICONS: Record<string, string> = {
  tool_call: "🔧",
  agent_session: "🤖",
  mcp_call: "🔌",
  http_request: "🌐",
  transform: "🔄",
  delay: "⏱️",
  condition: "◇",
  kanban_action: "📋",
  table_action: "📊",
};

export const TRIGGER_ICONS: Record<string, string> = {
  webhook: "🔗",
  fs_watch: "📁",
  schedule: "📅",
  manual: "👆",
  event: "📡",
};

export const AUTH_TYPES = [
  { value: "none", label: "None" },
  { value: "apikey", label: "API Key" },
  { value: "basic", label: "Basic Auth" },
  { value: "oauth", label: "OAuth 2.0 Bearer" },
];

export const API_KEY_LOCATIONS = [
  { value: "header", label: "Header" },
  { value: "query", label: "Query String" },
];

export const TRANSFORM_MODES = [
  { value: "template", label: "Template", desc: "Resolve {{...}} expressions into a string or JSON object" },
  { value: "llm", label: "LLM Transform", desc: "Send resolved input to an LLM for extraction, summarization, or reformatting" },
  { value: "script", label: "Script (Python)", desc: "Run a Python script with access to `data` (step outputs). Set `result` to return." },
];

export const KANBAN_ACTIONS = [
  { value: "add_card", label: "Add Card" },
  { value: "move_card", label: "Move Card" },
  { value: "update_card", label: "Update Card" },
];

export const TABLE_ACTIONS = [
  { value: "add_row", label: "Add Row" },
  { value: "update_row", label: "Update Row" },
  { value: "find_rows", label: "Find Rows" },
];
