import type { ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "./Icon";
import s from "./Collapsible.module.css";

export function Collapsible({
  summary,
  aside,
  boxed = false,
  defaultOpen = false,
  onToggle,
  children,
}: {
  summary: ReactNode;
  aside?: ReactNode;
  boxed?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <details className={cx(s.details, boxed && s.boxed)} open={defaultOpen || undefined} onToggle={onToggle && ((event) => onToggle(event.currentTarget.open))}>
      <summary className={s.summary}>
        {summary}
        <Icon name="chevron-down" className={s.chevron} />
        {aside && <span className={s.aside}>{aside}</span>}
      </summary>
      <div className={s.body}>{children}</div>
    </details>
  );
}
