import CodeMirror from "@uiw/react-codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { EditorState } from "@codemirror/state";
import type { Transaction } from "@codemirror/state";
import "./MarkdownEditor.css";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** When true, reject any edit that would make a line start with # */
  blockHeadings?: boolean;
  className?: string;
}

const headingBlocker = EditorState.transactionFilter.of((tr: Transaction) => {
  if (!tr.docChanged) return tr;
  let hasHeading = false;
  tr.changes.iterChangedRanges((_fromA, _toA, fromB, toB) => {
    const doc = tr.newDoc;
    const lineFrom = doc.lineAt(fromB).number;
    const lineTo = doc.lineAt(toB).number;
    for (let n = lineFrom; n <= lineTo; n++) {
      if (doc.line(n).text.startsWith("#")) {
        hasHeading = true;
      }
    }
  });
  return hasHeading ? [] : tr;
});

export default function MarkdownEditor({ value, onChange, blockHeadings, className }: Props) {
  const extensions = [markdown(), ...(blockHeadings ? [headingBlocker] : [])];

  return (
    <CodeMirror
      value={value}
      extensions={extensions}
      onChange={onChange}
      className={`markdown-editor${className ? ` ${className}` : ""}`}
      theme="none"
      basicSetup={{
        lineNumbers: false,
        foldGutter: false,
        dropCursor: false,
        highlightActiveLine: false,
        highlightActiveLineGutter: false,
      }}
    />
  );
}
