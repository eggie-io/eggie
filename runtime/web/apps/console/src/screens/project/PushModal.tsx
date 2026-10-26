import { useEffect, useState, type ReactNode } from "react";
import { Button, Card, Choice, Modal, Notice, ProgressBar, cx } from "@eggie/ui";
import { Elapsed } from "../../components/Elapsed";
import { openExternal } from "../../desktop/desktop";
import { pushPhaseCaption } from "../../projects/copy";
import { size } from "../../projects/format";
import { useJob, usePushTargets, useStartPush } from "../../projects/queries";
import type { Job, Project } from "../../projects/types";
import {
  defaultData,
  floatingWarnings,
  instanceRows,
  pushChoice,
  secretsUrl,
  type InstanceRow,
  type PushTargets,
} from "../../push/push";
import { AddressRow } from "./AddressRows";
import s from "./ProjectPage.module.css";

type PushResult = {
  instance_id: string;
  instance_name: string;
  instance_project: string | null;
  urls: string[];
  secrets_missing: number;
  open_secrets_url: string | null;
  local_restart: "ok" | "failed" | null;
};

type Pick = { id: string; data: boolean; confirmed: boolean };

const secretsLabel = (n: number) => `Enter ${n} secret${n === 1 ? "" : "s"} on the kitchen`;

function pushedOn(atSec: number): string {
  return `Pushed ${new Date(atSec * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
}

// The record's link stands in when the job's result has none, e.g. after reopening.
function recordSecretsUrl(targets: PushTargets | undefined, instanceId: string): string | null {
  const record = targets?.pushed[instanceId];
  const instance = targets?.instances.find((k) => k.id === instanceId);
  if (!record || !instance || !record.instance_project || record.secrets_missing === 0) return null;
  return secretsUrl(instance.open_console_url, record.instance_project);
}

export function PushModal({ project, open, onClose }: { project: Project; open: boolean; onClose: () => void }) {
  const targets = usePushTargets(project.id, open);
  const start = useStartPush(project.id);
  const [followed, setFollowed] = useState<string | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);

  // Reopening while a push runs picks it up again.
  const runningId = project.job?.kind === "push" ? project.job.id : null;
  if (open && followed === null && runningId !== null) setFollowed(runningId);

  const live = useJob(followed);
  const job: Job | undefined = live.data;
  const settled = job?.state === "done" || job?.state === "failed";
  const { refetch } = targets;
  // A finished push changes the records; a failed one may have learnt the instance already has
  // the project (already_on_instance), which turns on the repeat-push warning.
  useEffect(() => {
    if (open && settled) void refetch();
  }, [open, settled, refetch]);

  const close = () => {
    start.reset();
    setFollowed(null);
    setPick(null);
    setSentTo(null);
    onClose();
  };

  const rows = targets.data ? instanceRows(targets.data) : [];
  const chosen = rows.find((r) => r.pickable && r.id === pick?.id) ?? rows.find((r) => r.pickable) ?? null;
  const own = pick !== null && chosen !== null && pick.id === chosen.id ? pick : null;
  const data = own ? own.data : chosen ? defaultData(chosen) : false;
  const confirmed = own?.confirmed ?? false;
  const choice = chosen ? pushChoice(chosen, data, confirmed) : null;

  const select = (row: InstanceRow) => setPick({ id: row.id, data: defaultData(row), confirmed: false });
  const push = () => {
    if (!chosen) return;
    setPick({ id: chosen.id, data, confirmed });
    setSentTo(chosen.label);
    start.mutate(
      { instance_id: chosen.id, data, replace_data: data && confirmed },
      {
        onSuccess: ({ job_id }) => setFollowed(job_id),
        onError: () => void refetch(),
      },
    );
  };

  const title = `Push ${project.id} to a kitchen`;
  let body: ReactNode;
  let footer: ReactNode = null;

  if (followed !== null && (job === undefined || job.state === "running") && !live.isError) {
    const phase = job?.phase ?? project.job?.phase ?? "preparing";
    const caption = pushPhaseCaption(phase);
    const progress = phase === "sending" && job?.progress && job.progress.total > 0 ? job.progress : null;
    body = (
      <>
        <p className={s.pushHead}>{sentTo ? `Pushing ${project.id} to ${sentTo}` : `Pushing ${project.id}`}</p>
        <ProgressBar label={caption} value={progress ? progress.done / progress.total : undefined} />
        <div className={s.caption}>
          <span>{caption}</span>
          <span>
            {progress && `${size(progress.done)} of ${size(progress.total)} · `}
            <Elapsed startedAt={job?.started_at ?? project.job?.started_at ?? Date.now() / 1000} />
          </span>
        </div>
        <p className={s.footnote}>Closing this doesn't stop the push. Open it again to see how it's going.</p>
      </>
    );
  } else if (job?.state === "done") {
    const result = job.result as PushResult;
    const link = result.open_secrets_url ?? recordSecretsUrl(targets.data, result.instance_id);
    body = (
      <>
        <p className={s.pushHead}>Pushed to {result.instance_name}</p>
        {result.urls.length > 0 && (
          <Card as="ul" className={s.addresses}>
            {result.urls.map((url) => (
              <AddressRow key={url} url={url} label="On the kitchen" isPublic>
                <Button variant="ghost" size="sm" iconRight="external" onClick={() => openExternal(url)}>Open</Button>
              </AddressRow>
            ))}
          </Card>
        )}
        {link && (
          <Notice tone="warning">
            {project.id} waits on the kitchen until its {result.secrets_missing === 1 ? "secret has a value" : "secrets have values"}.
          </Notice>
        )}
        {result.local_restart === "failed" && (
          <Notice tone="warning">{project.id} didn't start again on this computer. Start it from its overview.</Notice>
        )}
      </>
    );
    footer = link ? (
      <Button variant="primary" iconRight="external" onClick={() => openExternal(link)}>{secretsLabel(result.secrets_missing)}</Button>
    ) : (
      <Button variant="primary" onClick={close}>Done</Button>
    );
  } else if (targets.isError && !targets.data) {
    body = <Notice tone="danger">{targets.error.message}</Notice>;
    footer = <Button disabled={targets.isFetching} onClick={() => void refetch()}>Try again</Button>;
  } else if (!targets.data) {
    body = <p className={s.muted}>Looking for your kitchens…</p>;
  } else if (targets.data.instances.length === 0) {
    const create = targets.data.create_instance_url;
    body = (
      <>
        <p className={s.pushHead}>You don't have a kitchen yet</p>
        <p className={s.modalSub}>A kitchen is your own server on the internet. Create one, then push {project.id} to it.</p>
      </>
    );
    footer = <Button variant="primary" iconRight="external" onClick={() => openExternal(create)}>Create a kitchen</Button>;
  } else {
    const t = targets.data;
    const failure = job?.state === "failed" ? job.detail : live.isError ? live.error.message : null;
    const recordLink = chosen ? recordSecretsUrl(t, chosen.id) : null;
    const missing = chosen ? (t.pushed[chosen.id]?.secrets_missing ?? 0) : 0;
    body = (
      <>
        {failure && <Notice tone="danger">{failure}</Notice>}
        {targets.isError && <Notice tone="danger">{targets.error.message}</Notice>}
        <ul className={s.instances} aria-label="Kitchens">
          {rows.map((row) => {
            const notes = [row.note, row.pushedAt !== null ? pushedOn(row.pushedAt) : null].filter(Boolean).join(" · ");
            const manage = row.manageUrl;
            return (
              <li key={row.id} className={s.instance}>
                <Choice
                  type="radio"
                  name={`push-instance-${project.id}`}
                  className={cx(s.instancePick, !row.pickable && s.instanceOff)}
                  checked={chosen?.id === row.id}
                  disabled={!row.pickable}
                  onChange={() => select(row)}
                  label={<><span className={s.instanceName}>{row.label}</span><span className={s.instanceHost}>{row.host}</span></>}
                  description={notes || undefined}
                />
                {manage && (
                  <Button variant="ghost" size="sm" iconRight="external" onClick={() => openExternal(manage)}>Manage</Button>
                )}
              </li>
            );
          })}
        </ul>
        <Button variant="ghost" size="sm" icon="plus" className={s.newInstance} onClick={() => openExternal(t.create_instance_url)}>
          Create a new kitchen
        </Button>
        {chosen && choice && (
          <>
            {choice.repeat && (
              <Notice tone="warning">This replaces {project.id}'s files on {chosen.label}. Changes made there are lost.</Notice>
            )}
            <Choice
              type="checkbox"
              label="Move data (databases)"
              checked={data}
              onChange={(e) => setPick({ id: chosen.id, data: e.target.checked, confirmed: false })}
            />
            {choice.needsConfirm && (
              <Choice
                type="checkbox"
                label="Replace the kitchen's data with the data on this computer"
                checked={confirmed}
                onChange={(e) => setPick({ id: chosen.id, data, confirmed: e.target.checked })}
              />
            )}
            {data && t.running && <Notice>{project.id} stops while its data is copied, then starts again.</Notice>}
          </>
        )}
        {floatingWarnings(t).map((warning) => <Notice key={warning} tone="warning">{warning}</Notice>)}
        {recordLink && (
          <Notice title={`${missing} ${missing === 1 ? "secret" : "secrets"} still to enter on ${chosen?.label}`}>
            <span>
              <Button variant="secondary" size="sm" iconRight="external" onClick={() => openExternal(recordLink)}>
                {secretsLabel(missing)}
              </Button>
            </span>
          </Notice>
        )}
        {start.error && <Notice tone="danger">{start.error.message}</Notice>}
      </>
    );
    footer = (
      <>
        <Button onClick={close}>Cancel</Button>
        <Button variant="primary" icon="upload" disabled={!choice?.canPush || start.isPending} onClick={push}>
          {failure ? "Try again" : "Push"}
        </Button>
      </>
    );
  }

  return (
    <Modal open={open} onClose={close} title={title} footer={footer}>
      {body}
    </Modal>
  );
}
