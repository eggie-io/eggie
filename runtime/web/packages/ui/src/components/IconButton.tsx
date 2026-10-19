import type { ButtonHTMLAttributes } from "react";
import { cx } from "../cx";
import type { IconName } from "../icons";
import { Button } from "./Button";
import { Icon } from "./Icon";
import s from "./IconButton.module.css";

export type IconButtonProps = {
  icon: IconName;
  label: string;
  size?: "sm" | "md";
  variant?: "ghost" | "secondary";
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children">;

export function IconButton({ icon, label, size = "md", variant = "ghost", className, ...rest }: IconButtonProps) {
  return (
    <Button {...rest} variant={variant} size={size} className={cx(s.square, s[size], className)} aria-label={label} title={label}>
      <Icon name={icon} />
    </Button>
  );
}
