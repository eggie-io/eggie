import type { ReactNode } from "react";
import { cx } from "../cx";
import type { IconName } from "../icons";
import { Icon } from "./Icon";
import s from "./Notice.module.css";

type Tone = "info" | "warning" | "danger" | "success";

const TONE_ICON: Record<Tone, IconName> = {
  info: "info",
  warning: "alert-triangle",
  danger: "x-circle",
  success: "check-circle",
};

export function Notice({
  tone = "info",
  title,
  icon,
  className,
  children,
}: {
  tone?: Tone;
  title?: ReactNode;
  icon?: "info" | "folder";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx(s.notice, s[tone], className)}>
      <Icon name={icon === "folder" ? "folder" : TONE_ICON[tone]} size="md" className={s.icon} />
      <div className={s.body}>
        {title && <strong className={s.title}>{title}</strong>}
        {children}
      </div>
    </div>
  );
}
