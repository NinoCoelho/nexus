import { forwardRef, useImperativeHandle, useRef } from "react";
import CodeMirror, { type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView } from "@codemirror/view";
import { EditorSelection, EditorState } from "@codemirror/state";
import type { Transaction } from "@codemirror/state";
import "./MarkdownEditor.css";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** When true, reject any edit that would make a line start with # */
  blockHeadings?: boolean;
  className?: string;
  wordWrap?: boolean;
}

export interface MarkdownEditorHandle {
  wrapSelection: (before: string, after: string) => void;
  insertAtLineStart: (prefix: string) => void;
  focus: () => void;
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

const MarkdownEditor = forwardRef<MarkdownEditorHandle, Props>(
  function MarkdownEditor({ value, onChange, blockHeadings, className, wordWrap }, ref) {
    const cmRef = useRef<ReactCodeMirrorRef>(null);

    useImperativeHandle(ref, () => ({
      wrapSelection(before: string, after: string) {
        const view = cmRef.current?.view;
        if (!view) return;
        const { state } = view;
        const { from, to } = state.selection.main;
        const selected = state.sliceDoc(from, to);
        const isWrapped = selected.startsWith(before) && selected.endsWith(after);
        const newText = isWrapped
          ? selected.slice(before.length, selected.length - after.length)
          : before + selected + after;
        // cursor goes: after unwrap → start of unwrapped text; after wrap → inside markers (or after selection+closer)
        const cursorAfter = isWrapped
          ? from
          : from + before.length + selected.length;
        const anchorAfter = isWrapped ? from : from + before.length;
        view.dispatch({ changes: { from, to, insert: newText } });
        view.dispatch({ selection: EditorSelection.range(anchorAfter, cursorAfter), scrollIntoView: true });
        view.focus();
      },
      insertAtLineStart(prefix: string) {
        const view = cmRef.current?.view;
        if (!view) return;
        const { state } = view;
        const { from } = state.selection.main;
        const line = state.doc.lineAt(from);
        if (line.text.startsWith(prefix)) {
          const newPos = Math.max(line.from, from - prefix.length);
          view.dispatch({ changes: { from: line.from, to: line.from + prefix.length, insert: "" } });
          view.dispatch({ selection: EditorSelection.cursor(newPos), scrollIntoView: true });
        } else {
          view.dispatch({ changes: { from: line.from, to: line.from, insert: prefix } });
          view.dispatch({ selection: EditorSelection.cursor(from + prefix.length), scrollIntoView: true });
        }
        view.focus();
      },
      focus() {
        cmRef.current?.view?.focus();
      },
    }));

    const extensions = [
      markdown(),
      ...(blockHeadings ? [headingBlocker] : []),
      ...(wordWrap ? [EditorView.lineWrapping] : []),
    ];

    return (
      <CodeMirror
        ref={cmRef}
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
);

export default MarkdownEditor;
