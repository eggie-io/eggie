import { useEffect, useRef, useState } from "react";
import { Badge, Button, IconButton, Notice, useCopy } from "@eggie/ui";
import { CopyButton } from "../../components/CopyButton";
import { dismissHint, hintDismissed, showsDns, showsHint, type CustomAddress, type DomainProblem } from "../../domains/domains";
import { useCheckDomain, useMakePrimary } from "../../domains/queries";
import { actionError } from "../../projects/copy";
import { domainPrompt } from "../../projects/prompts";
import { DomainStatus } from "./DomainStatus";
import s from "./DomainPage.module.css";

function RowMenu({ address, onPrimary, onRemove, busy }: {
  address: CustomAddress;
  onPrimary: () => void;
  onRemove: () => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>("[role='menuitem']")?.focus();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      root.current?.querySelector<HTMLButtonElement>("[aria-haspopup]")?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const pick = (action: () => void) => () => {
    setOpen(false);
    action();
  };

  return (
    <div className={s.menuRoot} ref={root}>
      <IconButton
        icon="more"
        label={`More for ${address.display}`}
        size="sm"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      />
      {open && (
        <div className={s.menu} role="menu">
          {!address.primary && (
            <button type="button" role="menuitem" className={s.menuItem} disabled={busy} onClick={pick(onPrimary)}>
              Make primary
            </button>
          )}
          <button type="button" role="menuitem" className={s.menuItem} onClick={pick(onRemove)}>
            Remove
          </button>
        </div>
      )}
    </div>
  );
}

function ProblemNotice({ problem }: { problem: DomainProblem }) {
  return (
    <Notice tone="warning" title={problem.message}>
      {problem.next_step && <p className={s.step}>{problem.next_step}</p>}
      {problem.seen && problem.expected && (
        <dl className={s.seen}>
          <div><dt>We see:</dt> <dd>{problem.seen.join(", ")}</dd></div>
          <div><dt>We expect:</dt> <dd>{problem.expected.join(", ")}</dd></div>
        </dl>
      )}
    </Notice>
  );
}

function DnsBox({ id, address }: { id: string; address: CustomAddress }) {
  const check = useCheckDomain(id);
  return (
    <div className={s.dnsBox}>
      {address.dns.length > 0 && (
        <>
          <p className={s.dnsLead}>Create this record where you bought the domain:</p>
          <table className={s.dns}>
            <thead>
              <tr><th scope="col">Type</th><th scope="col">Name</th><th scope="col">Value</th></tr>
            </thead>
            <tbody>
              {address.dns.map((record) => (
                <tr key={`${record.type} ${record.name} ${record.value}`}>
                  <td data-label="Type"><code>{record.type}</code></td>
                  <td data-label="Name"><code>{record.name}</code></td>
                  <td data-label="Value">
                    <span className={s.value}>
                      <code>{record.value}</code>
                      <CopyButton text={record.value} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <div className={s.checkRow}>
        <span className={s.note}>We check by ourselves; you can close this page.</span>
        <Button size="sm" icon="refresh" disabled={check.isPending} onClick={() => check.mutate(address.hostname)}>
          Check now
        </Button>
      </div>
      {check.error && <Notice tone="danger">{actionError(check.error)}</Notice>}
    </div>
  );
}

function ActiveHint({ id, hostname }: { id: string; hostname: string }) {
  const [dismissed, setDismissed] = useState(() => hintDismissed(id, hostname));
  const prompt = domainPrompt(id, hostname);
  const { copied, refused, copy, target } = useCopy<HTMLPreElement>(prompt);
  if (dismissed) return null;
  return (
    <Notice tone="info">
      Your app may still use its old address in its own settings (login callbacks, allowed hosts). Your coding agent can
      update it.
      {refused && <pre ref={target} className={s.prompt}>{prompt}</pre>}
      <div className={s.hintActions}>
        <Button size="sm" variant="primary" icon={copied ? "check" : "copy"} onClick={() => void copy()}>
          <span role="status" aria-live="polite">{copied ? "Copied" : "Copy a prompt"}</span>
        </Button>
        <Button size="sm" variant="ghost" onClick={() => { dismissHint(id, hostname); setDismissed(true); }}>
          Dismiss
        </Button>
      </div>
    </Notice>
  );
}

export function DomainRow({ id, address, onRemove }: { id: string; address: CustomAddress; onRemove: () => void }) {
  const primary = useMakePrimary(id);
  return (
    <li className={s.row}>
      <div className={s.line}>
        <span className={s.host}>{address.display}</span>
        {address.primary && <Badge tone="accent">Primary</Badge>}
        <span className={s.status}>
          <DomainStatus status={address.status} />
        </span>
        <RowMenu address={address} busy={primary.isPending} onPrimary={() => primary.mutate(address.hostname)} onRemove={onRemove} />
      </div>
      {primary.error && <Notice tone="danger">{actionError(primary.error)}</Notice>}
      {address.problem && <ProblemNotice problem={address.problem} />}
      {showsDns(address) && <DnsBox id={id} address={address} />}
      {showsHint(address) && <ActiveHint id={id} hostname={address.hostname} />}
    </li>
  );
}
