import { useId, useState, type ReactNode } from "react";
import { cx } from "../cx";
import s from "./TextField.module.css";

export function TextField({
  label,
  value,
  onChange,
  hint,
  error,
  autoFocus = false,
  placeholder,
  secret = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string;
  autoFocus?: boolean;
  placeholder?: string;
  secret?: boolean;
}) {
  const id = useId();
  const [shown, setShown] = useState(true);
  const note = error ?? hint;
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>{label}</label>
      <div className={s.wrap}>
      <input
        id={id}
        type={secret && !shown ? "password" : "text"}
        className={cx(s.input, secret && s.withEye, error !== undefined && s.invalid)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoFocus={autoFocus}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={error !== undefined || undefined}
        aria-describedby={note ? `${id}-note` : undefined}
      />
      {secret && (
        <button type="button" className={s.eye} aria-label={shown ? "Hide value" : "Show value"} onClick={() => setShown(!shown)}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
            <circle cx="12" cy="12" r="3" />
            {!shown && <path d="M4 4l16 16" />}
          </svg>
        </button>
      )}
      </div>
      {note && (
        <p id={`${id}-note`} className={error !== undefined ? s.error : s.hint} role={error !== undefined ? "alert" : undefined}>
          {note}
        </p>
      )}
    </div>
  );
}
