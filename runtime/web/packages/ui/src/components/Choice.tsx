import type { InputHTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import s from "./Choice.module.css";

export type ChoiceProps = {
  type: "radio" | "checkbox";
  label: ReactNode;
  description?: ReactNode;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

export function Choice({ type, label, description, className, ...rest }: ChoiceProps) {
  return (
    <label className={cx(s.choice, className)}>
      <input type={type} className={s.input} {...rest} />
      <span>
        {label}
        {description && <span className={s.description}>{description}</span>}
      </span>
    </label>
  );
}
