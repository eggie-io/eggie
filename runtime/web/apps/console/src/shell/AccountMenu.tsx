import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cx } from "@eggie/ui";
import type { Account } from "../account/account";
import { api as consoleApi, createApi } from "../api/client";
import { useDesktopHome } from "../desktop/DesktopContext";
import { browserLink, openExternal } from "../desktop/desktop";
import { REVOKE_URL, useDisconnectGitHub, useGitHub } from "../github/github";
import { githubView } from "../github/view";
import { GitHubModal } from "../screens/github/GitHubModal";
import { CHEVRON_DOWN, EXTERNAL, GITHUB, SIGN_OUT } from "../screens/icons";
import s from "./Shell.module.css";

const api = createApi((input, init) => fetch(input, init));

export function AccountMenu({ onSignedOut }: { onSignedOut: () => void }) {
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [github, setGitHub] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const home = useDesktopHome();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["account"],
    queryFn: () => api.get<Account>("/api/account"),
    refetchInterval: 60_000,
  });

  // Revoked from the web app, or signed out in another tab.
  useEffect(() => {
    if (data && data.state !== "signed_in") {
      // A later remount must start from a fresh fetch, not this stale answer.
      queryClient.removeQueries({ queryKey: ["account"] });
      onSignedOut();
    }
  }, [data, onSignedOut, queryClient]);

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

  const signOut = async () => {
    setLeaving(true);
    setError(null);
    try {
      await api.post("/api/account/sign-out");
      queryClient.removeQueries({ queryKey: ["account"] });
      onSignedOut();
    } catch {
      setLeaving(false);
      setError("Couldn't sign out. Try again.");
    }
  };

  const email = data?.state === "signed_in" ? data.email : null;

  return (
    <div className={s.account} ref={root}>
      <button
        type="button"
        className={s.avatarButton}
        aria-label="Account"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
      >
        <span className={s.avatar} aria-hidden="true">{initial(email)}</span>
        {CHEVRON_DOWN}
      </button>
      {/* Hidden rather than unmounted, so a half-finished disconnect confirmation survives a close. */}
      <div id={panelId} className={s.menu} hidden={!open}>
        {email && (
          <div className={s.who}>
            <span className={s.whoLabel}>Signed in as</span>
            <span className={s.whoEmail}>{email}</span>
          </div>
        )}
        <span className={s.rule} aria-hidden="true" />
        <GitHubLine onConnect={() => { setOpen(false); setGitHub(true); }} />
        {home && <OpenInBrowser />}
        <span className={s.rule} aria-hidden="true" />
        {error && <span className={s.menuError} role="alert">{error}</span>}
        <button type="button" className={cx(s.item, s.itemQuiet)} onClick={signOut} disabled={leaving}>
          {SIGN_OUT}<span className={s.itemLabel}>Sign out</span>
        </button>
      </div>
      <GitHubModal open={github} onClose={() => setGitHub(false)} />
    </div>
  );
}

function initial(email: string | null): string {
  const letter = email?.match(/[a-z0-9]/i)?.[0];
  return letter ? letter.toUpperCase() : "·";
}

function GitHubLine({ onConnect }: { onConnect: () => void }) {
  const status = useGitHub().data;
  const disconnect = useDisconnectGitHub();
  const [confirming, setConfirming] = useState(false);
  const [left, setLeft] = useState(false);
  if (!status) return null;
  const view = githubView(status);

  let detail: ReactNode;
  let actions: ReactNode;
  if (left && view.kind === "connect") {
    detail = (
      <span className={s.lineSub}>
        Disconnected.{" "}
        <a href={REVOKE_URL} onClick={(e) => { e.preventDefault(); openExternal(REVOKE_URL); }}>
          Remove Eggie's access on GitHub
        </a>{" "}
        to revoke it fully.
      </span>
    );
    actions = <button type="button" className={s.chip} onClick={() => { setLeft(false); onConnect(); }}>Connect</button>;
  } else if (view.kind === "ready" || view.kind === "applying" || view.kind === "setupFailed" || view.kind === "reconnect") {
    if (confirming) {
      detail = <span className={s.lineSub}>Disconnect @{view.login}?</span>;
      actions = (
        <>
          <button
            type="button"
            className={cx(s.chip, s.chipDanger)}
            disabled={disconnect.isPending}
            onClick={() => disconnect.mutate(undefined, { onSuccess: () => { setLeft(true); setConfirming(false); } })}
          >
            Disconnect
          </button>
          <button type="button" className={s.chip} onClick={() => setConfirming(false)}>Keep</button>
        </>
      );
    } else {
      detail = (
        <span className={s.lineSubMono}>
          @{view.login}{view.kind === "reconnect" && <span className={s.lineWarn}> · needs reconnecting</span>}
        </span>
      );
      actions = (
        <>
          {view.kind === "reconnect" && <button type="button" className={s.chip} onClick={onConnect}>Reconnect</button>}
          <button type="button" className={cx(s.chip, s.chipDanger)} onClick={() => setConfirming(true)}>Disconnect</button>
        </>
      );
    }
  } else {
    detail = <span className={s.lineSub}>Not connected</span>;
    actions = <button type="button" className={s.chip} onClick={onConnect}>Connect</button>;
  }

  return (
    <div className={s.line}>
      {GITHUB}
      <span className={s.lineText}>
        <span className={s.lineTitle}>GitHub</span>
        {detail}
      </span>
      <span className={s.lineActions}>{actions}</span>
    </div>
  );
}

// Codes last a minute; one fetched on hover is used well inside that.
const FRESH_MS = 40_000;

function OpenInBrowser() {
  const [busy, setBusy] = useState(false);
  const ready = useRef<{ url: string; at: number } | null>(null);

  // Fetched ahead of the click: WKWebView blocks a new window opened after
  // an await, once the click's user gesture has lapsed.
  const prepare = () => {
    if (ready.current && Date.now() - ready.current.at < FRESH_MS) return;
    void browserLink(consoleApi, window.location.origin).then((url) => {
      if (url.includes("#handoff=")) ready.current = { url, at: Date.now() };
    });
  };

  return (
    <button
      type="button"
      className={s.item}
      disabled={busy}
      onPointerEnter={prepare}
      onPointerDown={prepare}
      onFocus={prepare}
      onClick={async () => {
        const prepared = ready.current;
        ready.current = null;
        if (prepared && Date.now() - prepared.at < FRESH_MS) return openExternal(prepared.url);
        setBusy(true);
        try {
          openExternal(await browserLink(consoleApi, window.location.origin));
        } finally {
          setBusy(false);
        }
      }}
    >
      {EXTERNAL}
      <span className={s.itemLabel}>Open in browser</span>
      {window.location.port && <span className={s.itemHint}>:{window.location.port}</span>}
    </button>
  );
}
