import { Fragment, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Card, Icon, Notice, cx } from "@eggie/ui";
import { api, ApiError } from "../../api/client";
import { useNow } from "../../projects/useNow";
import { filesRoute, joinPath } from "../../uploads/paths";
import { useListing } from "../../uploads/queries";
import { useUploads } from "../../uploads/QueueProvider";
import { fits } from "../../uploads/queue";
import type { Disk } from "../../uploads/uploadApi";
import { DestinationModal } from "./DestinationModal";
import { DoneCard } from "./DoneCard";
import { Columns, Listing } from "./Listing";
import { UploadPanel } from "./UploadPanel";
import { TitleRow } from "../project/TitleRow";
import s from "./FilesPage.module.css";

export function FilesPage() {
  const params = useParams();
  const id = params.id ?? "";
  const dir = joinPath(params["*"] ?? "");
  const listing = useListing(id, dir);
  const { queue, items, heldBy, landed, dismiss } = useUploads(id);
  const navigate = useNavigate();
  const now = useNow();
  const client = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const [dialog, setDialog] = useState<{ files: File[]; chooser: boolean } | null>(null);
  const [folderRefused, setFolderRefused] = useState(false);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    void queue.syncPending(id);
  }, [queue, id]);

  // A file dropped outside the listing would make the browser open it and leave the app.
  useEffect(() => {
    const stay = (event: Event) => event.preventDefault();
    window.addEventListener("dragover", stay);
    window.addEventListener("drop", stay);
    return () => {
      window.removeEventListener("dragover", stay);
      window.removeEventListener("drop", stay);
    };
  }, []);

  async function dropInto(files: File[]) {
    const disk = await client.fetchQuery({ queryKey: ["disk"], queryFn: () => api.get<Disk>("/api/disk"), staleTime: 0 });
    const fitting = files.filter((f) => fits(f.size, disk.free_bytes));
    if (fitting.length > 0) queue.add(id, dir, fitting);
    const tooBig = files.filter((f) => !fitting.includes(f));
    if (tooBig.length > 0) setDialog({ files: tooBig, chooser: false });
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const files: File[] = [];
    let folder = false;
    for (const item of Array.from(event.dataTransfer.items)) {
      if (item.kind !== "file") continue;
      // A folder shows up as a File too; only the entry API tells them apart.
      if (item.webkitGetAsEntry()?.isDirectory) {
        folder = true;
        continue;
      }
      const f = item.getAsFile();
      if (f) files.push(f);
    }
    setFolderRefused(folder);
    if (files.length > 0) void dropInto(files).catch(() => setDialog({ files, chooser: true }));
  }

  const code = listing.error instanceof ApiError ? listing.error.code : null;
  useEffect(() => {
    if (code === "folder_not_found" && dir !== "") navigate(filesRoute(id, ""), { replace: true });
  }, [code, dir, id, navigate]);

  if (code === "project_not_found") {
    return (
      <section className={s.page}>
        <TitleRow title={`No project called ${id}`} />
      </section>
    );
  }

  const segments = dir === "" ? [] : dir.split("/");
  const entries = listing.data?.entries ?? [];
  const hint = "Drag files in from your desktop, or use Upload to choose where they land.";

  let body: ReactNode;
  if (code === "permission_denied") {
    body = <div className={s.bodyPad}><Notice tone="warning">Eggie can't look inside this folder — a program in the project owns it.</Notice></div>;
  } else if (listing.isError) {
    body = <div className={s.bodyPad}><Notice tone="danger">{listing.error.message}</Notice></div>;
  } else if (listing.data === undefined) {
    body = <div className={s.bodyPad}><p className={s.muted}>Looking in the cupboard…</p></div>;
  } else if (entries.length === 0) {
    body = (
      <div className={s.empty}>
        <Icon name="upload" size="md" />
        <p>{hint}</p>
      </div>
    );
  } else {
    body = <Listing projectId={id} dir={dir} entries={entries} now={now} />;
  }

  return (
    <section className={s.page}>
      <TitleRow
        title="Files"
        sub="Everything in this project’s folder"
        actions={<Button variant="primary" icon="upload" onClick={() => picker.current?.click()}>Upload</Button>}
      />
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          if (files.length > 0) setDialog({ files, chooser: true });
        }}
      />
      {landed && <DoneCard item={landed} onDismiss={dismiss} />}
      {heldBy && heldBy.projectId !== id && items.some((item) => item.state === "waiting") && (
        <Notice tone="warning">
          <p className={s.noticeText}>
            Uploads are waiting — {heldBy.name} in {heldBy.projectId} ran out of room. Free up space in the desktop app, then carry on.
          </p>
          <div className={s.actions}>
            <Button size="sm" onClick={() => void queue.carryOn().catch(() => false)}>I've freed some up — carry on</Button>
            <Button size="sm" variant="ghost" className={s.giveUp} onClick={() => queue.remove(heldBy.key)}>Give up on {heldBy.name}</Button>
          </div>
        </Notice>
      )}
      <UploadPanel items={items} queue={queue} />
      {folderRefused && <Notice tone="warning">Folders don't go up here — zip it first, or bring a whole folder in as a project with "From a folder" on the projects page.</Notice>}
      <div
        className={cx(s.drop, dragging && s.dragging)}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <Card as="section" className={s.card}>
          <div className={s.cardHead}>
            <span className={s.headIcon}><Icon name="folder" /></span>
            <nav className={s.crumbs} aria-label="Folder">
              <Link to={filesRoute(id, "")} className={s.crumb} aria-current={segments.length === 0 ? "page" : undefined} title={id}>{id}</Link>
              {segments.map((segment, index) => (
                <Fragment key={index}>
                  <span className={s.sep} aria-hidden="true">/</span>
                  <Link
                    to={filesRoute(id, segments.slice(0, index + 1).join("/"))}
                    className={s.crumb}
                    aria-current={index === segments.length - 1 ? "page" : undefined}
                    title={segment}
                  >
                    {segment}
                  </Link>
                </Fragment>
              ))}
            </nav>
            {entries.length > 0 && <span className={s.count}>{entries.length} {entries.length === 1 ? "thing" : "things"} in here</span>}
          </div>
          {entries.length > 0 && <Columns />}
          <div className={s.body}>{body}</div>
        </Card>
      </div>
      {entries.length > 0 && <p className={s.hint}>{hint}</p>}
      <DestinationModal
        projectId={id}
        files={dialog?.files ?? []}
        startDir={dir}
        chooser={dialog?.chooser ?? true}
        open={dialog !== null}
        onClose={() => setDialog(null)}
        onSend={(target, files) => queue.add(id, target, files)}
        onPickAgain={() => {
          setDialog(null);
          picker.current?.click();
        }}
      />
    </section>
  );
}
