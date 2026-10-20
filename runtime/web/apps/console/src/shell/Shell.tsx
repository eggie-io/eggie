import type { ReactNode } from "react";
import { BrandMark, Icon, StatusDot, cx } from "@eggie/ui";
import { useDesktopHome } from "../desktop/DesktopContext";
import { InstanceSwitcher } from "./InstanceSwitcher";
import s from "./Shell.module.css";

export function Shell({
  tone = "yolk",
  signedIn = false,
  nav,
  trailing,
  children,
}: {
  tone?: "yolk" | "cold";
  signedIn?: boolean;
  nav?: ReactNode;
  trailing?: ReactNode;
  children?: ReactNode;
}) {
  const home = useDesktopHome();
  return (
    <div className={s.page}>
      <header className={cx(s.header, tone === "cold" && s.muted)}>
        <div className={s.bar}>
          {home && (
            <a className={s.back} href={home} title="Back to Eggie" aria-label="Back to Eggie">
              <Icon name="chevron-left" />
            </a>
          )}
          <span className={s.mark}><BrandMark size={18} /></span>
          {signedIn ? <InstanceSwitcher /> : <span className={s.brand}>Eggie</span>}
          {signedIn && <span className={s.open}><StatusDot status="running" label="Open" /></span>}
          {trailing && <div className={s.end}>{trailing}</div>}
        </div>
        {nav && <nav className={s.nav}>{nav}</nav>}
      </header>
      <main className={s.content}>{children}</main>
    </div>
  );
}
