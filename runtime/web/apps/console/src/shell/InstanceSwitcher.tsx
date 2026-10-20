import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Button, Icon, cx } from "@eggie/ui";
import { createApi } from "../api/client";
import { openExternal } from "../desktop/desktop";
import { useDesktopHome } from "../desktop/DesktopContext";
import { switcherEntries, type Kitchen, type SwitcherEntry } from "../instances/instances";
import { openKitchenUrl, webAppKitchens } from "../site/signIn";
import { useSite, useWebApp } from "../site/SiteContext";
import s from "./Shell.module.css";

const api = createApi((input, init) => fetch(input, init));

export function InstanceSwitcher() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const site = useSite();
  const webApp = useWebApp();
  const desktopHome = useDesktopHome();
  const { data, isError, isFetching, refetch } = useQuery({
    queryKey: ["instances"],
    queryFn: () => api.get<{ instances: Kitchen[] }>("/api/instances"),
    enabled: open,
    staleTime: 0,
    retry: false,
  });

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const kitchens = data?.instances;
  const current = kitchens?.find((kitchen) => kitchen.current);
  const label = site === null ? "This computer" : (current?.display_name ?? site ?? "Kitchen");
  const entries = kitchens ? switcherEntries({ kitchens, site }) : [];
  const listed = kitchens?.length ?? 0;

  const linkFor = (entry: SwitcherEntry) =>
    entry.target !== null && webApp ? openKitchenUrl(webApp, entry.target.host) : null;
  const choose = (entry: SwitcherEntry) => {
    const url = linkFor(entry);
    if (url === null) return;
    // The desktop window keeps no web app sign-in; the system browser has one.
    if (desktopHome !== null) {
      setOpen(false);
      openExternal(url);
    } else {
      window.location.assign(url);
    }
  };
  const kitchensPage = webAppKitchens(webApp);

  return (
    <div className={s.switcher} ref={root}>
      <Button
        variant="ghost"
        iconRight="chevron-down"
        className={s.switcherButton}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
      >
        <span className={s.brand}>{label}</span>
      </Button>
      <div id={panelId} className={s.switcherMenu} hidden={!open}>
        {isError && (
          <div className={s.switcherNote} role="alert">
            <span>Couldn't load your kitchens.</span>
            <Button size="sm" disabled={isFetching} onClick={() => void refetch()}>Retry</Button>
          </div>
        )}
        {!kitchens && !isError && <span className={s.switcherNote}>Loading…</span>}
        {kitchens &&
          entries.map((entry) => {
            const clickable = linkFor(entry) !== null;
            return (
              <button
                key={entry.key}
                type="button"
                className={cx(s.item, entry.current && s.switcherCurrent)}
                disabled={!clickable}
                aria-current={entry.current ? "true" : undefined}
                onClick={() => choose(entry)}
              >
                <span className={s.itemLabel}>
                  {entry.label}
                  {entry.note && <span className={s.lineSub}>{entry.note}</span>}
                </span>
                {entry.current && <Icon name="check" />}
              </button>
            );
          })}
        {kitchens && listed === 0 && (
          <span className={s.switcherNote}>
            {kitchensPage ? (
              <a href={kitchensPage}>Kitchens are created in the Eggie web app</a>
            ) : (
              "Kitchens are created in the Eggie web app"
            )}
          </span>
        )}
      </div>
    </div>
  );
}
