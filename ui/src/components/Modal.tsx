/**
 * Modal — generic confirm/prompt dialog.
 *
 * Two variants controlled by `kind`:
 *   - "confirm" → Yes/No buttons (optionally styled as danger)
 *   - "prompt"  → text input + OK/Cancel
 *
 * Escape key always cancels. Enter submits (prompt mode only).
 * Clicking the backdrop cancels.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import "./Modal.css";

interface BaseProps {
  title: string;
  message?: string;
  onCancel: () => void;
}

interface PromptProps extends BaseProps {
  kind: "prompt";
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  allowEmpty?: boolean;
  onSubmit: (value: string) => void;
}

interface ConfirmProps extends BaseProps {
  kind: "confirm";
  confirmLabel?: string;
  danger?: boolean;
  onSubmit: () => void;
}

export type ModalProps = PromptProps | ConfirmProps;

export default function Modal(props: ModalProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(
    props.kind === "prompt" ? (props.defaultValue ?? "") : "",
  );

  useEffect(() => {
    if (props.kind === "prompt") inputRef.current?.select();
  }, [props.kind]);

  const handleKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") props.onCancel();
      if (e.key === "Enter" && props.kind === "prompt") {
        e.preventDefault();
        const p = props as PromptProps;
        if (p.allowEmpty || value.trim()) p.onSubmit(value);
      }
    },
    [props, value],
  );

  return (
    <div className="modal-backdrop" onClick={props.onCancel}>
      <div
        className="modal-dialog"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKey}
      >
        <div className="modal-title">{props.title}</div>
        {props.message && <div className="modal-message">{props.message}</div>}
        {props.kind === "prompt" && (
          <input
            ref={inputRef}
            className="modal-input"
            type="text"
            value={value}
            placeholder={props.placeholder}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
          />
        )}
        <div className="modal-actions">
          <button className="modal-btn" onClick={props.onCancel}>
            Cancel
          </button>
          <button
            className={`modal-btn modal-btn--primary${
              props.kind === "confirm" && props.danger ? " modal-btn--danger" : ""
            }`}
            onClick={() => {
              if (props.kind === "prompt") {
                if (!props.allowEmpty && !value.trim()) return;
                props.onSubmit(value);
              } else {
                props.onSubmit();
              }
            }}
            disabled={props.kind === "prompt" && !props.allowEmpty && !value.trim()}
          >
            {props.kind === "prompt"
              ? (props.confirmLabel ?? "OK")
              : (props.confirmLabel ?? "Confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
