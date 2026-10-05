import { useNavigate } from "react-router";
import { Button, Collapsible, Notice, RowCard, cx } from "@eggie/ui";
import { folderHeading, relativeTime } from "../../projects/format";
import { useAdopt } from "../../projects/queries";
import { slugify } from "../../projects/slugify";
import type { Discovered } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { FOLDER } from "../icons";
import s from "./ProjectList.module.css";

function Folder({ folder, now, adopting, onAdopt }: { folder: Discovered; now: number; adopting: boolean; onAdopt: () => void }) {
  const seen = `Turned up ${relativeTime(folder.seen_at, now)}`;
  if (folder.adoptable) {
    return (
      <RowCard className={s.folder}>
        <div className={s.folderText}>
          <span className={s.name}>{folder.name}</span>
          <span className={s.quiet}>{seen} · knows how to start itself</span>
        </div>
        <Button variant="primary" disabled={adopting} onClick={onAdopt}>Adopt it</Button>
      </RowCard>
    );
  }
  const rename = slugify(folder.name);
  return (
    <RowCard className={cx(s.folder, s.blocked)}>
      <div className={s.folderText}>
        <span className={s.name}>{folder.name}</span>
        {folder.reason === "bad_name" ? (
          <span className={s.quiet}>
            Its name has characters an address can't use — ask your coding agent to rename the folder
            {rename ? <> to <strong>{rename}</strong></> : null}.
          </span>
        ) : (
          <>
            <span className={s.quiet}>
              Eggie can't take this one in yet — the folder doesn't say how to run itself. Ask your coding agent to add
              the start-up recipe and it'll show up here, ready to adopt.
            </span>
            <Collapsible summary="What Eggie looks for">
              <pre className={s.recipe}>{`~/projects/${folder.name}\n  docker-compose.yml  — missing`}</pre>
            </Collapsible>
          </>
        )}
      </div>
      <Button disabled>Can't adopt</Button>
    </RowCard>
  );
}

export function DiscoveredBand({ folders }: { folders: Discovered[] }) {
  const adopt = useAdopt();
  const navigate = useNavigate();
  const now = useNow(30_000);
  return (
    <section className={s.band}>
      <header className={s.bandHead}>
        {FOLDER}
        <div>
          <h2 className={s.bandTitle}>{folderHeading(folders.length)}</h2>
          <p className={s.bandSub}>Your coding agent made these. Eggie hasn't met them yet.</p>
        </div>
      </header>
      <ul className={s.folders}>
        {folders.map((folder) => (
          <li key={folder.name}>
            <Folder
              folder={folder}
              now={now}
              adopting={adopt.isPending && adopt.variables === folder.name}
              onAdopt={() =>
                adopt.mutate(folder.name, { onSuccess: (created) => navigate(`/p/${encodeURIComponent(created.id)}`) })
              }
            />
          </li>
        ))}
      </ul>
      {adopt.error && <Notice>{adopt.error.message}</Notice>}
    </section>
  );
}
