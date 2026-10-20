import type { ReactNode } from "react";
import { Button, Card, Modal, Notice } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { publicView } from "../../projects/public";
import { usePublic } from "../../projects/queries";
import type { Project } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { openExternal } from "../../desktop/desktop";
import { AddressRow } from "./AddressRows";
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
  let footer: ReactNode = null;
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
        </>
      );
      footer = <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Make it public</Button>;
      break;
    case "enabling":
      body = <p className={s.modalSub}>Turning on…</p>;
      break;
    case "on":
      body = (
        <>
          <Card as="ul" className={s.addresses}>
            {view.urls.map((entry) => (
              <AddressRow key={entry.url} url={entry.url} label={entry.service} isPublic>
                <Button variant="ghost" size="sm" iconRight="external" onClick={() => openExternal(entry.url)}>Open</Button>
              </AddressRow>
            ))}
          </Card>
          {project.status !== "started_ok" && <Notice tone="warning">Start the project so visitors can see it.</Notice>}
        </>
      );
      footer = (
        <>
          {view.left && <span className={s.footNote}>{view.left}</span>}
          <Button variant="danger" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>Turn off</Button>
        </>
      );
      break;
    case "failed":
      body = <Notice tone="danger">{view.message}</Notice>;
      footer = (
        <>
          <Button variant="ghost" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>Dismiss</Button>
          <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Try again</Button>
        </>
      );
      break;
  }

  return (
    <Modal open={open} onClose={close} title={`Public address for ${project.id}`} footer={footer}>
      {body}
      {toggle.error && <Notice tone="danger">{actionError(toggle.error)}</Notice>}
    </Modal>
  );
}
