import type { ReactNode } from "react";
import { cx } from "../cx";
import type { IconName } from "../icons";
import { Icon } from "./Icon";
import s from "./Badge.module.css";

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info" | "gold" | "accent" | "outline";

export function Badge({
  tone = "neutral",
  icon,
  className,
  children,
}: {
  tone?: BadgeTone;
  icon?: IconName;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.badge, s[tone], className)}>
      {icon && <Icon name={icon} className={s.icon} />}
      {children}
    </span>
  );
}
