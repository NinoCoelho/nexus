export type TriggerType = "webhook" | "fs_watch" | "schedule" | "manual" | "event";
export type StepType =
  | "tool_call"
  | "agent_session"
  | "mcp_call"
  | "http_request"
  | "condition"
  | "transform"
  | "delay"
  | "kanban_action"
  | "table_action"
  | "return_step";

export type RunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type StepRunStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface TriggerConfig {
  id: string;
  type: TriggerType;
  token?: string;
  cron?: string;
  path?: string;
  pattern?: string;
  events?: string[];
  debounce_ms?: number;
  event?: string;
  filter?: Record<string, unknown>;
}

export interface StepConfig {
  id: string;
  name: string;
  type: StepType;
  slug?: string;
  tool?: string;
  input?: Record<string, unknown>;
  prompt?: string;
  model?: string;
  background?: boolean;
  max_turns?: number;
  condition?: string;
  on_error?: string;
  retry_count?: number;
  retry_delay_seconds?: number;
  url?: string;
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  auth_type?: string;
  auth_credential?: string;
  auth_username?: string;
  auth_password_credential?: string;
  auth_header_name?: string;
  auth_prefix?: string;
  auth_query_name?: string;
  auth_location?: string;
  custom_headers?: Record<string, string>;
  expression?: string;
  then_step?: string;
  else_step?: string;
  template?: string;
  output_format?: string;
  duration_seconds?: number;
  mcp_server?: string;
  mcp_tool?: string;
  action?: string;
  board_path?: string;
  lane_id?: string;
  card_id?: string;
  table_path?: string;
  row_data?: Record<string, unknown>;
  row_id?: string;
  where?: Record<string, unknown>;
  query_sql?: string;
  llm_instructions?: string;
  output_sample?: string;
  response_template?: string;
  output_schema?: string;
  next_step?: string;
}

export interface WorkflowDef {
  title: string;
  enabled: boolean;
  triggers: TriggerConfig[];
  variables: Record<string, string>;
  steps: StepConfig[];
  description?: string;
}

export interface WorkflowSummary {
  path: string;
  title: string;
  enabled: boolean;
  step_count: number;
  trigger_count: number;
}

export interface WorkflowRun {
  id: string;
  workflow_path: string;
  trigger_id: string;
  trigger_type: TriggerType;
  trigger_payload: Record<string, unknown>;
  status: RunStatus;
  started_at: string;
  finished_at?: string;
  current_step?: string;
  error?: string;
}

export interface StepRun {
  run_id: string;
  step_id: string;
  step_name: string;
  step_slug: string;
  step_type: string;
  status: StepRunStatus;
  input_resolved?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  started_at?: string;
  finished_at?: string;
  condition_branches?: Record<string, string>;
}

export interface RunDetail {
  run: WorkflowRun;
  steps: StepRun[];
}

export interface DebugStepEvent {
  run_id: string;
  step_id: string;
  step_name?: string;
  step_type?: string;
  status?: StepRunStatus;
  input_resolved?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  started_at?: string;
  finished_at?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: {
    type?: string;
    properties?: Record<string, {
      type?: string;
      description?: string;
      enum?: string[];
      items?: { type?: string };
      default?: unknown;
    }>;
    required?: string[];
  };
}

export interface StepSchema {
  slug: string;
  output_schema?: { keys: string[]; types: Record<string, string> };
  sample_output?: unknown;
}

export interface InteractiveRunState {
  run: WorkflowRun;
  steps: StepRun[];
  condition_branches: Record<string, string>;
}

export interface InteractiveStepEvent {
  run_id: string;
  step_id?: string;
  step_name?: string;
  step_type?: string;
  status?: StepRunStatus;
  input_resolved?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  started_at?: string;
  finished_at?: string;
  condition_result?: boolean;
  condition_branch?: string;
  trigger_payload?: Record<string, unknown>;
}

export interface EventType {
  pattern: string;
  description: string;
  category: string;
}

export interface VaultFolder {
  path: string;
  name: string;
}

export type TestTriggerStatus = "listening" | "captured" | "timeout" | "error";

export interface TestTriggerEvent {
  type: "test.listening" | "test.captured" | "test.timeout" | "test.error";
  test_id?: string;
  payload?: Record<string, unknown>;
  url?: string;
  error?: string;
}
