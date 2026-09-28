import { cx } from "@omelet/ui";
import { CopyButton } from "../../components/CopyButton";
import { hostOf } from "../../projects/format";
import type { PublicUrl, WebEntry } from "../../projects/types";
import s from "./ProjectPage.module.css";

export function AddressRows({ web, live, publicUrls }: { web: WebEntry[]; live: boolean; publicUrls?: PublicUrl[] }) {
  if (web.length === 0) return null;
  return (
    <section>
      <h2 className={s.label}>Where to find it</h2>
      <ul className={s.addresses}>
        {web.map((entry) => (
          <li key={entry.url} className={cx(s.address, !live && s.dim)}>
            <span className={s.addressLabel}>{entry.primary ? "The app itself" : entry.service}</span>
            <span className={s.url}>{hostOf(entry.url)}</span>
            {live && <CopyButton text={entry.url} />}
            {publicUrls
              ?.filter((p) => p.local_url === entry.url)
              .map((p) => (
                <span key={p.url} className={s.publicLine}>
                  Public · <span className={s.url}>{hostOf(p.url)}</span> <CopyButton text={p.url} />
                </span>
              ))}
          </li>
        ))}
      </ul>
    </section>
  );
}
