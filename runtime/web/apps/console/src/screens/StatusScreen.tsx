import type { ReactNode } from "react";
import { Shell } from "../shell/Shell";
import s from "./StatusScreen.module.css";

export function StatusScreen({
  tone = "cold",
  art,
  title,
  actions,
  footer,
  children,
}: {
  tone?: "yolk" | "cold";
  art: ReactNode;
  title: string;
  actions: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Shell tone={tone}>
      <div className={s.center}>
        {art}
        <div className={s.text}>
          <h1 className={s.title}>{title}</h1>
          {children}
        </div>
        {actions && <div className={s.actions}>{actions}</div>}
        {footer && <p className={s.footer}>{footer}</p>}
      </div>
    </Shell>
  );
}
