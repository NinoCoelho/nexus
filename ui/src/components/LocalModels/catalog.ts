/**
 * Curated catalog of free, local-runnable models exposed in the UI as
 * one-click installs. Each entry pins a repo + filename so the picker
 * stays simple — if a file goes missing we surface a friendly error
 * rather than dragging the user into raw search results.
 *
 * Naming convention here is deliberate: titles and descriptions never
 * leak words like "GGUF", "quantization", "params", or vendor hub names.
 */

export interface CatalogEntry {
  /** Stable id for React keys + persistence. */
  id: string;
  /** Friendly human-readable title shown to the user. */
  title: string;
  /** One-line plain-language description (no jargon). */
  description: string;
  /** Best-fit short tag(s) — max two — like "Fast", "Multilingual". */
  badges: string[];
  /** Approximate disk/RAM footprint in GB; informational only. */
  approx_size_gb: number;
  /** RAM threshold below which we hide the Install button. */
  min_ram_gb: number;
  /** Backing repo id (not shown to the user). */
  repo_id: string;
  /** Backing filename (not shown to the user). */
  filename: string;
}

export const CATALOG: CatalogEntry[] = [
  {
    id: "llama-3.2-3b",
    title: "Llama 3.2",
    description: "Fast everyday chat. Works on most laptops.",
    badges: ["Fast"],
    approx_size_gb: 2.0,
    min_ram_gb: 6,
    repo_id: "bartowski/Llama-3.2-3B-Instruct-GGUF",
    filename: "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
  },
  {
    id: "phi-3.5-mini",
    title: "Phi 3.5 mini",
    description: "Compact and surprisingly capable. Great on small machines.",
    badges: ["Compact"],
    approx_size_gb: 2.4,
    min_ram_gb: 6,
    repo_id: "bartowski/Phi-3.5-mini-instruct-GGUF",
    filename: "Phi-3.5-mini-instruct-Q4_K_M.gguf",
  },
  {
    id: "gemma-3-4b",
    title: "Gemma 3",
    description: "Google's open model, balanced for chat.",
    badges: ["Balanced"],
    approx_size_gb: 2.6,
    min_ram_gb: 8,
    repo_id: "bartowski/gemma-3-4b-it-GGUF",
    filename: "gemma-3-4b-it-Q4_K_M.gguf",
  },
  {
    id: "qwen-2.5-7b",
    title: "Qwen 2.5",
    description: "Strong with Portuguese, English, and code.",
    badges: ["Multilingual"],
    approx_size_gb: 4.6,
    min_ram_gb: 10,
    repo_id: "bartowski/Qwen2.5-7B-Instruct-GGUF",
    filename: "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
  },
  {
    id: "glm-4.7-flash",
    title: "GLM 4.7 Flash",
    description: "Thinks step-by-step before answering. Best for analysis.",
    badges: ["Reasoning", "Larger"],
    approx_size_gb: 19.0,
    min_ram_gb: 32,
    repo_id: "unsloth/GLM-4.7-Flash-GGUF",
    filename: "GLM-4.7-Flash-Q4_K_M.gguf",
  },
];
