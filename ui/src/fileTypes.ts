/**
 * File-type classifier + icons.
 *
 * Single source of truth used by the vault tree, the file preview, and the
 * editor panel so they agree on how to render each extension.
 */

import type { ReactElement } from "react";
import React from "react";

export type FileKind =
  | "markdown"
  | "image"
  | "pdf"
  | "video"
  | "audio"
  | "code"
  | "csv"
  | "json"
  | "text"
  | "archive"
  | "binary";

export interface FileClassification {
  kind: FileKind;
  /** Highlight.js-style language hint for code files (e.g. "python"). */
  language?: string;
  /** Human-readable label (e.g. "PDF", "Image"). */
  label: string;
}

const CODE_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  rb: "ruby",
  rs: "rust",
  go: "go",
  java: "java",
  kt: "kotlin",
  swift: "swift",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cs: "csharp",
  php: "php",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  fish: "bash",
  sql: "sql",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  xml: "xml",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  less: "less",
  lua: "lua",
  r: "r",
  scala: "scala",
  dart: "dart",
};

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"]);
const VIDEO_EXT = new Set(["mp4", "webm", "mov", "m4v", "mkv", "ogv"]);
const AUDIO_EXT = new Set(["mp3", "wav", "ogg", "flac", "m4a", "opus", "aac"]);
const TEXT_EXT = new Set(["txt", "log", "ini", "env", "conf", "cfg"]);
const ARCHIVE_EXT = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"]);

export function getExtension(path: string): string {
  const base = path.split("/").pop() ?? path;
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return "";
  return base.slice(dot + 1).toLowerCase();
}

export function classify(path: string): FileClassification {
  const ext = getExtension(path);
  if (ext === "md" || ext === "mdx" || ext === "markdown") return { kind: "markdown", label: "Markdown" };
  if (IMAGE_EXT.has(ext)) return { kind: "image", label: "Image" };
  if (ext === "pdf") return { kind: "pdf", label: "PDF" };
  if (VIDEO_EXT.has(ext)) return { kind: "video", label: "Video" };
  if (AUDIO_EXT.has(ext)) return { kind: "audio", label: "Audio" };
  if (ext === "json") return { kind: "json", label: "JSON" };
  if (ext === "csv" || ext === "tsv") return { kind: "csv", label: "CSV" };
  if (CODE_EXT[ext]) return { kind: "code", language: CODE_EXT[ext], label: ext.toUpperCase() };
  if (TEXT_EXT.has(ext) || ext === "") return { kind: "text", label: "Text" };
  if (ARCHIVE_EXT.has(ext)) return { kind: "archive", label: "Archive" };
  return { kind: "binary", label: ext ? ext.toUpperCase() : "File" };
}

// ── Icons (hand-drawn SVG to match existing sidebar/tree style) ───────────

type IconProps = { size?: number };

function svg(children: ReactElement | ReactElement[], size = 13): ReactElement {
  const kids = Array.isArray(children) ? children : [children];
  return React.createElement(
    "svg",
    {
      width: size,
      height: size,
      viewBox: "0 0 20 20",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 1.6,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    },
    ...kids,
  );
}

const h = React.createElement;

function FilePageIcon(extra: ReactElement | ReactElement[], size?: number) {
  return svg(
    [
      h("path", { key: "p", d: "M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" }),
      h("polyline", { key: "c", points: "12 2 12 6 16 6" }),
      ...(Array.isArray(extra) ? extra : [extra]),
    ],
    size,
  );
}

export function MarkdownIcon({ size }: IconProps = {}) {
  return FilePageIcon(
    [
      h("path", { key: "m1", d: "M6 14V10l2 2 2-2v4" }),
      h("path", { key: "m2", d: "M12 10v4 M12 14l1.5-1.5 M12 14l-1.5-1.5" }),
    ],
    size,
  );
}

export function ImageIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("rect", { key: "r", x: 3, y: 4, width: 14, height: 12, rx: 1.5 }),
      h("circle", { key: "c", cx: 8, cy: 9, r: 1.3 }),
      h("path", { key: "p", d: "M4 14l3.5-3.5 3 3 2-2L17 14" }),
    ],
    size,
  );
}

export function PdfIcon({ size }: IconProps = {}) {
  return FilePageIcon(
    h("text", {
      key: "t",
      x: 6,
      y: 15,
      fontSize: 5,
      fontWeight: 700,
      fill: "currentColor",
      stroke: "none",
    }, "PDF"),
    size,
  );
}

export function VideoIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("rect", { key: "r", x: 3, y: 5, width: 11, height: 10, rx: 1.5 }),
      h("path", { key: "p", d: "M14 9l4-2v6l-4-2z" }),
    ],
    size,
  );
}

export function AudioIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("path", { key: "p", d: "M7 15V5l9-2v10" }),
      h("circle", { key: "c1", cx: 5, cy: 15, r: 2 }),
      h("circle", { key: "c2", cx: 14, cy: 13, r: 2 }),
    ],
    size,
  );
}

export function CodeIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("polyline", { key: "l", points: "7 6 3 10 7 14" }),
      h("polyline", { key: "r", points: "13 6 17 10 13 14" }),
      h("line", { key: "s", x1: 11, y1: 4, x2: 9, y2: 16 }),
    ],
    size,
  );
}

export function CsvIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("rect", { key: "r", x: 3, y: 4, width: 14, height: 12, rx: 1 }),
      h("line", { key: "h1", x1: 3, y1: 8, x2: 17, y2: 8 }),
      h("line", { key: "h2", x1: 3, y1: 12, x2: 17, y2: 12 }),
      h("line", { key: "v1", x1: 9, y1: 4, x2: 9, y2: 16 }),
      h("line", { key: "v2", x1: 13, y1: 4, x2: 13, y2: 16 }),
    ],
    size,
  );
}

export function JsonIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("path", { key: "l", d: "M8 3H5a1 1 0 0 0-1 1v4a1 1 0 0 1-1 1 1 1 0 0 1 1 1v4a1 1 0 0 0 1 1h3" }),
      h("path", { key: "r", d: "M12 3h3a1 1 0 0 1 1 1v4a1 1 0 0 0 1 1 1 1 0 0 0-1 1v4a1 1 0 0 1-1 1h-3" }),
    ],
    size,
  );
}

export function TextIcon({ size }: IconProps = {}) {
  return FilePageIcon(
    [
      h("line", { key: "l1", x1: 7, y1: 10, x2: 13, y2: 10 }),
      h("line", { key: "l2", x1: 7, y1: 13, x2: 13, y2: 13 }),
      h("line", { key: "l3", x1: 7, y1: 16, x2: 11, y2: 16 }),
    ],
    size,
  );
}

export function ArchiveIcon({ size }: IconProps = {}) {
  return svg(
    [
      h("rect", { key: "r", x: 3, y: 4, width: 14, height: 12, rx: 1 }),
      h("line", { key: "m", x1: 10, y1: 4, x2: 10, y2: 16, strokeDasharray: "2 1.5" }),
    ],
    size,
  );
}

export function BinaryIcon({ size }: IconProps = {}) {
  return FilePageIcon([], size);
}

export function iconFor(path: string, size = 13): ReactElement {
  const { kind } = classify(path);
  switch (kind) {
    case "markdown": return MarkdownIcon({ size });
    case "image": return ImageIcon({ size });
    case "pdf": return PdfIcon({ size });
    case "video": return VideoIcon({ size });
    case "audio": return AudioIcon({ size });
    case "code": return CodeIcon({ size });
    case "csv": return CsvIcon({ size });
    case "json": return JsonIcon({ size });
    case "text": return TextIcon({ size });
    case "archive": return ArchiveIcon({ size });
    case "binary": return BinaryIcon({ size });
  }
}

// ── Formatting helpers used by tooltips + preview headers ─────────────────

export function formatBytes(n: number | undefined): string {
  if (n == null || !isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(kb < 10 ? 1 : 0)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

export function formatRelativeTime(mtimeSec: number | undefined): string {
  if (mtimeSec == null) return "";
  const ms = mtimeSec < 1e12 ? mtimeSec * 1000 : mtimeSec;
  const diff = Date.now() - ms;
  if (diff < 0) return "just now";
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}
