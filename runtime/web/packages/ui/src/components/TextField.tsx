import { useId, type ReactNode } from "react";
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
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string;
  autoFocus?: boolean;
  placeholder?: string;
  type?: "text" | "password";
}) {
  const id = useId();
  const note = error ?? hint;
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>{label}</label>
      <input
        id={id}
        type={type}
        className={cx(s.input, error !== undefined && s.invalid)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoFocus={autoFocus}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={error !== undefined || undefined}
        aria-describedby={note ? `${id}-note` : undefined}
      />
      {note && (
        <p id={`${id}-note`} className={error !== undefined ? s.error : s.hint} role={error !== undefined ? "alert" : undefined}>
          {note}
        </p>
      )}
    </div>
  );
}
