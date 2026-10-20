import type { ReactNode } from "react";
import { useNavigate } from "react-router";
import { Button, Modal, Notice } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { deleteRows } from "../../projects/format";
import { useDeletePreview, useDeleteProject } from "../../projects/queries";
import s from "./ProjectPage.module.css";

export function DeleteModal({ id, open, onClose }: { id: string; open: boolean; onClose: () => void }) {
  const preview = useDeletePreview(id, open);
  const remove = useDeleteProject(id);
  const navigate = useNavigate();

  const close = () => {
    remove.reset();
    onClose();
  };

  let contents: ReactNode;
  if (preview.data) {
    const rows = deleteRows(preview.data);
    contents =
      rows.length === 0 ? (
        <p className={s.muted}>It's empty — only its name goes.</p>
      ) : (
        <ul className={s.bin}>
          {rows.map((row) => (
            <li key={row.label} className={s.binRow}>
              <span className={s.binLabel}>{row.label}</span>
              <span className={s.binDetail}>{row.detail}</span>
              {row.names && (
                <ul className={s.names}>
                  {row.names.map((name) => <li key={name}><code>{name}</code></li>)}
                </ul>
              )}
            </li>
          ))}
        </ul>
      );
  } else if (preview.isError) {
    contents = <Notice tone="danger">{preview.error.message}</Notice>;
  } else {
    contents = <p className={s.muted}>Counting what's in there…</p>;
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title={`Throw out ${id}?`}
      footer={
        <>
          <span className={s.footNote}>Other projects aren't touched.</span>
          <Button onClick={close}>Keep it</Button>
          <Button
            variant="danger"
            disabled={remove.isPending || preview.isPending}
            onClick={() => remove.mutate(true, { onSuccess: (result) => navigate("/", { state: { deleted: result } }) })}
          >
            Yes, throw it out
          </Button>
        </>
      }
    >
      <p className={s.modalSub}>This clears the whole counter. Here's exactly what goes in the bin:</p>
      {contents}
      <Notice tone="warning">There's no undo and no bin to fish it out of. Anything you already downloaded stays yours; nothing else does.</Notice>
      {remove.error && <Notice tone="danger">{actionError(remove.error)}</Notice>}
    </Modal>
  );
}
