import type { ButtonHTMLAttributes } from "react";
import { cx } from "../cx";
import type { IconName } from "../icons";
import { Icon } from "./Icon";
import s from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  icon?: IconName;
  iconRight?: IconName;
}

export function Button({
  variant = "secondary",
  size = "md",
  icon,
  iconRight,
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button type={type} className={cx(s.button, s[size], s[variant], className)} {...rest}>
      {icon && <Icon name={icon} />}
      {children}
      {iconRight && <Icon name={iconRight} />}
    </button>
  );
}
