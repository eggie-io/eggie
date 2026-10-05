import { useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Button, Collapsible, Notice, ProgressBar, RowCard } from "@eggie/ui";
import { size } from "../../projects/format";
import { failure, into, summary } from "../../uploads/copy";
import { secondsLeft, timeLeftWords } from "../../uploads/eta";
import { filesRoute } from "../../uploads/paths";
import type { UploadItem, UploadQueue } from "../../uploads/queue";
import s from "./FilesPage.module.css";

const percent = (item: UploadItem) => (item.size === 0 ? 100 : Math.floor((item.offset / item.size) * 100));

export function UploadPanel({ items, queue }: { items: readonly UploadItem[]; queue: UploadQueue }) {
  // Keyed to the holding item, so a later hold never shows an earlier one's answer.
  const [stillFull, setStillFull] = useState<{ key: string; free: number } | null>(null);
  if (items.length === 0) return null;
  const full = items.find((item) => item.state === "noRoom" && item.uploadId !== null);

  async function carryOn() {
    await queue.carryOn().catch(() => {});
    // Read the queue, not `items`: this closure's copy predates carryOn's update.
    const holding = queue.snapshot().find((i) => i.state === "noRoom" && i.uploadId !== null);
    setStillFull(holding ? { key: holding.key, free: holding.freeBytes ?? 0 } : null);
  }

  return (
    <>
      {full && (
        <RowCard accent="trouble" className={s.banner}>
          <h2 className={s.bannerTitle}>Eggie ran out of room partway through</h2>
          <p className={s.bannerText}>
            {full.name} got {percent(full)}% of the way in. What made it is safe, and it can carry on from there — but you'll need to
            free up space in the desktop app first.
          </p>
          <Button variant="primary" onClick={carryOn}>I've freed some up — carry on</Button>
          {stillFull?.key === full.key && <Notice>Still not enough room — {size(stillFull.free)} free.</Notice>}
        </RowCard>
      )}
      <Collapsible boxed defaultOpen summary="Carrying things in" aside={summary(items)}>
        <ul className={s.queue}>
          {items.map((item) => (
            <UploadRow key={item.key} item={item} queue={queue} onCarryOn={carryOn} />
          ))}
        </ul>
        <p className={s.note}>Pausing is fine — nothing is lost. If you close this page, uploads pick up where they stopped when you're back.</p>
      </Collapsible>
    </>
  );
}

function UploadRow({ item, queue, onCarryOn }: { item: UploadItem; queue: UploadQueue; onCarryOn: () => void }) {
  const picker = useRef<HTMLInputElement>(null);
  const [wrongFile, setWrongFile] = useState(false);
  const got = `${size(item.offset)} of ${size(item.size)}`;
  const left = item.state === "going" ? secondsLeft(item.samples, item.size - item.offset) : null;

  let line: string;
  let actions: ReactNode = null;
  switch (item.state) {
    case "going":
      line = item.busy ? "Waiting for the project to finish starting" : `${got} · ${into(item.dir)}${left === null ? "" : ` · ${timeLeftWords(left)}`}`;
      actions = !item.busy && <Button size="md" onClick={() => queue.pause(item.key)}>Pause</Button>;
      break;
    case "waiting":
      line = `${size(item.size)} · waiting its turn`;
      actions = <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>;
      break;
    case "paused":
      line = `Paused at ${percent(item)}% · ${into(item.dir)}`;
      actions = (
        <>
          <Button onClick={() => queue.resume(item.key)}>Carry on</Button>
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
        </>
      );
      break;
    case "stalled":
      line = `The ${item.fromReload ? "page was closed" : "connection dropped"} at ${percent(item)}%. Nothing was lost — it can carry on from there.`;
      actions = (
        <>
          <Button onClick={() => (item.file ? queue.resume(item.key) : picker.current?.click())}>Pick up where it stopped</Button>
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
          <input
            ref={picker}
            type="file"
            hidden
            onChange={(event) => {
              const picked = event.target.files?.[0];
              event.target.value = "";
              if (picked) setWrongFile(!queue.relink(item.key, picked));
            }}
          />
        </>
      );
      break;
    case "noRoom":
      line = item.uploadId !== null ? `Stopped at ${percent(item)}% · ${got} got through · ${into(item.dir)}` : `Won't fit — ${size(item.size)}, with ${size(item.freeBytes ?? 0)} of room left`;
      actions = (
        <>
          <Button onClick={onCarryOn}>Carry on</Button>
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
        </>
      );
      break;
    case "failed":
      line = failure(item);
      actions =
        item.reason === "file_exists" ? (
          <>
            <Button onClick={() => queue.replace(item.key)}>Replace</Button>
            <Button variant="quiet" onClick={() => queue.remove(item.key)}>Skip</Button>
          </>
        ) : (
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
        );
      break;
    case "done":
      line = `In ${item.dir === "" ? "the top of the project" : `${item.dir}/`} · ${size(item.size)}`;
      actions = <Link to={filesRoute(item.projectId, item.dir)} className={s.showMe}>Show me</Link>;
      break;
  }

  return (
    <li className={s.upload}>
      <div className={s.uploadHead}>
        <strong className={s.uploadName}>{item.name}</strong>
        {(item.state === "going" || item.state === "paused") && <span className={s.meta}>{percent(item)}%</span>}
        <span className={s.uploadActions}>{actions}</span>
      </div>
      {item.state === "going" && <ProgressBar value={item.offset / Math.max(1, item.size)} label={`${item.name} upload`} />}
      <p className={item.state === "failed" || item.state === "noRoom" ? s.uploadTrouble : s.uploadLine}>{line}</p>
      {wrongFile && (
        <Notice>
          That's not the same file — pick {item.name} ({size(item.size)}).
        </Notice>
      )}
    </li>
  );
}
