import { useNavigate } from "react-router";
import { Button, Card, Collapsible, Notice } from "@eggie/ui";
import { folderHeading, relativeTime } from "../../projects/format";
import { useAdopt } from "../../projects/queries";
import { slugify } from "../../projects/slugify";
import type { Discovered } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import s from "./ProjectList.module.css";

function Folder({ folder, now, adopting, onAdopt }: { folder: Discovered; now: number; adopting: boolean; onAdopt: () => void }) {
  const seen = `Turned up ${relativeTime(folder.seen_at, now)}`;
  if (folder.adoptable) {
    return (
      <li className={s.folder}>
        <div className={s.folderText}>
          <span className={s.folderName}>{folder.name}</span>
          <span className={s.quiet}>{seen} · knows how to start itself</span>
        </div>
        <Button variant="primary" size="sm" disabled={adopting} onClick={onAdopt}>Adopt it</Button>
      </li>
    );
  }
  const rename = slugify(folder.name);
  return (
    <li className={s.folder}>
      <div className={s.folderText}>
        <span className={s.folderName}>{folder.name}</span>
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
      <Button size="sm" disabled>Can't adopt</Button>
    </li>
  );
}

export function DiscoveredBand({ folders }: { folders: Discovered[] }) {
  const adopt = useAdopt();
  const navigate = useNavigate();
  const now = useNow(30_000);
  return (
    <Notice tone="warning" icon="folder">
      <div className={s.band}>
        <h2 className={s.bandTitle}>{folderHeading(folders.length)}</h2>
        <p className={s.bandSub}>Your coding agent made these. Eggie hasn't met them yet.</p>
        <Card as="ul" className={s.folders}>
          {folders.map((folder) => (
            <Folder
              key={folder.name}
              folder={folder}
              now={now}
              adopting={adopt.isPending && adopt.variables === folder.name}
              onAdopt={() =>
                adopt.mutate(folder.name, { onSuccess: (created) => navigate(`/p/${encodeURIComponent(created.id)}`) })
              }
            />
          ))}
        </Card>
        {adopt.error && <Notice tone="danger">{adopt.error.message}</Notice>}
      </div>
    </Notice>
  );
}
