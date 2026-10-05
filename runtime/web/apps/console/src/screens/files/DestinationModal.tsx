import { useEffect, useState } from "react";
import { Button, Modal, Notice, TextField } from "@eggie/ui";
import { size } from "../../projects/format";
import { folderNameError, joinPath } from "../../uploads/paths";
import { useDisk, useListing } from "../../uploads/queries";
import { fits } from "../../uploads/queue";
import s from "./FilesPage.module.css";

export function DestinationModal({
  projectId,
  files,
  startDir,
  chooser,
  open,
  onClose,
  onSend,
  onPickAgain,
}: {
  projectId: string;
  files: File[];
  startDir: string;
  chooser: boolean;
  open: boolean;
  onClose: () => void;
  onSend: (dir: string, files: File[]) => void;
  onPickAgain: () => void;
}) {
  const top = useListing(projectId, "");
  const disk = useDisk(open);
  const [choice, setChoice] = useState(startDir);
  const [newFolder, setNewFolder] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setChoice(startDir);
      setNewFolder(null);
    }
  }, [open, startDir]);

  const folders = (top.data?.entries ?? []).filter((e) => e.kind === "folder").map((e) => e.name);
  const choices = ["", ...folders, ...(startDir !== "" && !folders.includes(startDir) ? [startDir] : [])];
  const nameError = newFolder === null ? null : folderNameError(newFolder);
  const target = newFolder === null ? choice : joinPath(choice, newFolder.trim());
  const free = disk.data?.free_bytes;
  const tooBig = free === undefined ? [] : files.filter((f) => !fits(f.size, free));
  const fitting = files.filter((f) => !tooBig.includes(f));
  const total = files.reduce((sum, f) => sum + f.size, 0);
  const preview = files.length === 1 ? `${projectId}/${joinPath(target, files[0].name)}` : `${projectId}/${target === "" ? "" : `${target}/`}`;

  return (
    <Modal open={open} onClose={onClose} title={chooser ? "Where should this land?" : "This one won't fit"}>
      {chooser && <p className={s.modalSub}>Pick the folder it belongs in — no rummaging around later.</p>}
      <p className={s.picked}>
        <strong>{files.length === 1 ? files[0].name : `${files.length} files`}</strong> <span>{size(total)}</span>
      </p>
      {tooBig.length > 0 && free !== undefined && (
        <Notice>
          {tooBig.length === 1 && files.length === 1 ? "This one won't fit — the" : `${tooBig.map((f) => f.name).join(", ")} won't fit — the`}{" "}
          {tooBig.length === 1 ? `file is ${size(tooBig[0].size)}` : `files need ${size(tooBig.reduce((n, f) => n + f.size, 0))}`} and Eggie has{" "}
          {size(free)} of room left. Make some space in the desktop app, then come back and send it up — we'll still be here.
        </Notice>
      )}
      {chooser && (
        <>
          <ul className={s.choices}>
            {choices.map((dir) => (
              <li key={dir || "(top)"}>
                <label className={s.choice}>
                  <input type="radio" name="destination" checked={choice === dir} onChange={() => setChoice(dir)} />
                  {dir === "" ? <span><strong>{projectId}</strong> — the top of the project</span> : <span>{dir}</span>}
                </label>
              </li>
            ))}
          </ul>
          {newFolder === null ? (
            <Button variant="quiet" onClick={() => setNewFolder("")}>New folder</Button>
          ) : (
            <TextField label="New folder" value={newFolder} onChange={setNewFolder} autoFocus error={newFolder === "" ? undefined : (nameError ?? undefined)} />
          )}
          <p className={s.path}>{preview}</p>
        </>
      )}
      <div className={s.modalFoot}>
        {chooser && (
          <Button
            variant="primary"
            disabled={free === undefined || fitting.length === 0 || nameError !== null}
            onClick={() => {
              onSend(target, fitting);
              onClose();
            }}
          >
            Send it up
          </Button>
        )}
        {tooBig.length > 0 && <Button onClick={onPickAgain}>Pick a smaller file</Button>}
        <Button variant="quiet" onClick={onClose}>Cancel</Button>
      </div>
      {chooser && <p className={s.note}>Big files are fine — you can pause and come back.</p>}
      {disk.isError && <Notice>{disk.error.message}</Notice>}
    </Modal>
  );
}
