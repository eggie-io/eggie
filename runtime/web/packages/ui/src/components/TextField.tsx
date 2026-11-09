import { useId, useState, type ReactNode } from "react";
import { cx } from "../cx";
import type { IconName } from "../icons";
import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
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
  icon,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string;
  autoFocus?: boolean;
  placeholder?: string;
  secret?: boolean;
  icon?: IconName;
}) {
  const id = useId();
  const [shown, setShown] = useState(true);
  const note = error ?? hint;
  const invalid = error !== undefined;
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>{label}</label>
      <div className={cx(s.control, invalid && s.invalid)}>
        {icon && <Icon name={icon} className={s.icon} />}
        <input
          id={id}
          type={secret && !shown ? "password" : "text"}
          className={s.input}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoFocus={autoFocus}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          aria-invalid={invalid || undefined}
          aria-describedby={note ? `${id}-note` : undefined}
        />
        {secret && (
          <IconButton
            icon={shown ? "eye" : "eye-off"}
            label={shown ? "Hide value" : "Show value"}
            size="sm"
            className={s.eye}
            onClick={() => setShown(!shown)}
          />
        )}
      </div>
      {note && (
        <p id={`${id}-note`} className={invalid ? s.error : s.hint} role={invalid ? "alert" : undefined}>
          {note}
        </p>
      )}
    </div>
  );
}
