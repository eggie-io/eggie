import type { ReactNode } from "react";
import { Egg, cx } from "@eggie/ui";
import { useDesktopHome } from "../desktop/DesktopContext";
import { BACK } from "../screens/icons";
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
      <header className={cx(s.bar, tone === "cold" && s.muted)}>
        {home && (
          <a className={s.home} href={home} title="Back to Home" aria-label="Back to Home">{BACK}</a>
        )}
        <div className={s.place}>
          <Egg tone={tone} size={22} />
          <span className={s.brand}>{signedIn ? "Kitchen" : "Eggie"}</span>
          {signedIn && <span className={s.open}><span className={s.dot} aria-hidden="true" />Open</span>}
        </div>
        {nav && (
          <>
            <span className={s.divider} aria-hidden="true" />
            <nav className={s.nav}>{nav}</nav>
          </>
        )}
        {trailing && <div className={s.end}>{trailing}</div>}
      </header>
      <main className={s.content}>{children}</main>
    </div>
  );
}
