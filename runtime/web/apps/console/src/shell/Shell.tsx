import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrandMark, Icon, StatusDot, cx } from "@eggie/ui";
import { useDesktopHome } from "../desktop/DesktopContext";
import { instancesQuery } from "../instances/query";
import { instancePage } from "../site/signIn";
import { useSite, useWebApp } from "../site/SiteContext";
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
  const site = useSite();
  const webApp = useWebApp();
  return (
    <div className={s.page}>
      <header className={cx(s.header, tone === "cold" && s.muted)}>
        <div className={s.bar}>
          {home ? (
            <Back href={home} />
          ) : typeof site === "string" ? (
            signedIn ? <SiteBack webApp={webApp} /> : <Back href={instancePage(webApp, null)} />
          ) : null}
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

function Back({ href }: { href: string | null }) {
  if (href === null) return null;
  return (
    <a className={s.back} href={href} title="Back to Eggie" aria-label="Back to Eggie">
      <Icon name="chevron-left" />
    </a>
  );
}

// Only signed in: the instance list needs the query client and a session.
function SiteBack({ webApp }: { webApp: string | null | undefined }) {
  const { data } = useQuery({ ...instancesQuery, staleTime: Infinity });
  return <Back href={instancePage(webApp, data?.instances.find((instance) => instance.current)?.id)} />;
}
