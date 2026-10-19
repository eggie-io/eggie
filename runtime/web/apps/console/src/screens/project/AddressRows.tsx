import { Fragment, type ReactNode } from "react";
import { Card, Icon } from "@eggie/ui";
import { CopyButton } from "../../components/CopyButton";
import { hostOf } from "../../projects/format";
import type { PublicUrl, WebEntry } from "../../projects/types";
import s from "./ProjectPage.module.css";

export function AddressRow({ url, label, isPublic = false, live = true, children }: {
  url: string;
  label: string;
  isPublic?: boolean;
  live?: boolean;
  children?: ReactNode;
}) {
  return (
    <li className={s.address}>
      <span className={s.addressIcon}><Icon name={isPublic ? "globe" : "lock"} /></span>
      <span className={s.addressLabel}>{label}</span>
      {live ? (
        <a className={s.url} href={url} target="_blank" rel="noopener noreferrer" title={url}>{hostOf(url)}</a>
      ) : (
        <span className={s.urlDim} title={url}>{hostOf(url)}</span>
      )}
      <span className={s.addressActions}>
        {live && <CopyButton text={url} />}
        {children}
      </span>
    </li>
  );
}

export function AddressRows({ web, live, publicUrls }: { web: WebEntry[]; live: boolean; publicUrls?: PublicUrl[] }) {
  if (web.length === 0) return null;
  return (
    <section className={s.section}>
      <h2 className={s.label}>Where to find it</h2>
      <Card as="ul" className={s.addresses}>
        {web.map((entry) => (
          <Fragment key={entry.url}>
            <AddressRow url={entry.url} label={entry.primary ? "The app itself" : entry.service} live={live} />
            {publicUrls
              ?.filter((p) => p.local_url === entry.url)
              .map((p) => <AddressRow key={p.url} url={p.url} label="Public" isPublic />)}
          </Fragment>
        ))}
      </Card>
    </section>
  );
}
