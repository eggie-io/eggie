import type { ReactNode } from "react";
import { Button, Modal, Notice } from "@omelet/ui";
import { actionError } from "../../projects/copy";
import { hostOf } from "../../projects/format";
import { publicView } from "../../projects/public";
import { usePublic } from "../../projects/queries";
import type { Project } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { openExternal } from "../../desktop/desktop";
import { ARROW } from "../icons";
import { CopyButton } from "../../components/CopyButton";
import s from "./ProjectPage.module.css";

export function PublicModal({ project, open, onClose }: { project: Project; open: boolean; onClose: () => void }) {
  const now = useNow();
  const toggle = usePublic(project.id);
  const view = publicView(project.public, now);
  const close = () => {
    toggle.reset();
    onClose();
  };

  let body: ReactNode;
  switch (view.kind) {
    case "unavailable":
      body = <p className={s.modalSub}>{view.message}</p>;
      break;
    case "off":
      body = (
        <>
          {view.note && <Notice>{view.note}</Notice>}
          <p className={s.modalSub}>
            Anyone with the link can open {project.id} until it expires. You get a new address each time.
          </p>
          <div className={s.modalFoot}>
            <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Make it public</Button>
          </div>
        </>
      );
      break;
    case "enabling":
      body = <p className={s.modalSub}>Turning on…</p>;
      break;
    case "on":
      body = (
        <>
          <ul className={s.addresses}>
            {view.urls.map((entry) => (
              <li key={entry.url} className={s.address}>
                <span className={s.addressLabel}>{entry.service}</span>
                <span className={s.url}>{hostOf(entry.url)}</span>
                <CopyButton text={entry.url} />
                <Button variant="quiet" onClick={() => openExternal(entry.url)}>Open{ARROW}</Button>
              </li>
            ))}
          </ul>
          {project.status !== "started_ok" && <Notice>Start the project so visitors can see it.</Notice>}
          <div className={s.modalFoot}>
            <Button variant="danger" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>Turn off</Button>
            {view.left && <span className={s.note}>{view.left}</span>}
          </div>
        </>
      );
      break;
    case "failed":
      body = (
        <>
          <Notice>{view.message}</Notice>
          <div className={s.modalFoot}>
            <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Try again</Button>
            <Button variant="quiet" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>Dismiss</Button>
          </div>
        </>
      );
      break;
  }

  return (
    <Modal open={open} onClose={close} title={`Public address for ${project.id}`}>
      {body}
      {toggle.error && <Notice>{actionError(toggle.error)}</Notice>}
    </Modal>
  );
}
