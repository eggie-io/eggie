import { useEffect, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Modal, Notice, ProgressBar, cx } from "@eggie/ui";
import { api } from "../../api/client";
import { createImportApi } from "../../imports/importApi";
import { fromDrop, fromFileList } from "../../imports/pick";
import { planImport, type ImportPlan, type Picked } from "../../imports/plan";
import { runImport, type Mode, type Phase } from "../../imports/send";
import { packGzip } from "../../imports/tar";
import { size } from "../../projects/format";
import { fits } from "../../uploads/queue";
import type { Disk } from "../../uploads/uploadApi";
import { FOLDER } from "../icons";
import s from "./ProjectList.module.css";

type Step =
  | { kind: "pick"; problem: string | null }
  | { kind: "reading" }
  | { kind: "review"; plan: ImportPlan; mode: Mode }
  | { kind: "working"; plan: ImportPlan; phase: Phase }
  | { kind: "failed"; plan: ImportPlan; mode: Mode; message: string };

const PICK: Step = { kind: "pick", problem: null };
const importApi = createImportApi();

function phaseLine(phase: Phase): string {
  if (phase.kind === "packing") return phase.total === 0 ? "Packing…" : `Packing · ${Math.floor((phase.read / phase.total) * 100)}%`;
  return `Sending · ${size(phase.sent)} of ${size(phase.total)}`;
}

function fraction(phase: Phase): number {
  return phase.kind === "packing" ? (phase.total === 0 ? 1 : phase.read / phase.total) : phase.total === 0 ? 1 : phase.sent / phase.total;
}

export function ImportFolderModal({ open, onClose, existing }: { open: boolean; onClose: () => void; existing: readonly string[] }) {
  const [step, setStep] = useState<Step>(PICK);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const client = useQueryClient();

  // Bumped whenever a pick is abandoned, so a folder the browser or the
  // planner finishes with afterwards doesn't reappear in a fresh dialog.
  const pickRun = useRef(0);
  // The run a click on "Choose a folder…" started; a change event for any other is stale.
  const opened = useRef(-1);

  const reset = () => {
    pickRun.current += 1;
    setStep(PICK);
  };

  const close = () => {
    if (step.kind === "working") return;
    reset();
    onClose();
  };

  // The native picker fires `cancel` when dismissed; React has no prop for it on
  // an input. It bubbles, and the <dialog> around it closes itself on `cancel`.
  useEffect(() => {
    const input = picker.current;
    if (!input) return;
    const cancelled = (event: Event) => {
      event.stopPropagation();
      if (opened.current === pickRun.current) reset();
    };
    input.addEventListener("cancel", cancelled);
    return () => input.removeEventListener("cancel", cancelled);
  }, [open]);

  // Nothing tells the page that a folder was picked until the browser has
  // listed every file in it, which takes seconds on a big folder. The window
  // getting focus back means the picker closed, so the loader starts there;
  // a `cancel` arriving after it sends the dialog back to the pick step.
  function choose() {
    const run = ++pickRun.current;
    opened.current = run;
    const pickerClosed = () => {
      if (pickRun.current === run) setStep((current) => (current.kind === "pick" ? { kind: "reading" } : current));
    };
    window.addEventListener("focus", pickerClosed, { once: true });
    picker.current?.click();
  }

  async function plan(picked: Picked[], run: number) {
    setStep({ kind: "reading" });
    const planned = await planImport(picked);
    if (pickRun.current === run) review(planned);
  }

  function review(plan: ImportPlan) {
    if (plan.name === "") {
      setStep({ kind: "pick", problem: "That folder is empty — start from scratch instead and it comes to the same thing." });
      return;
    }
    if (plan.id === "") {
      setStep({ kind: "pick", problem: `"${plan.name}" has no characters an address can use — rename the folder first.` });
      return;
    }
    if (plan.entries.length === 0) {
      setStep({ kind: "pick", problem: `Nothing in "${plan.name}" would go in — everything there is left out (${plan.skipped.join(", ")}).` });
      return;
    }
    setStep({ kind: "review", plan, mode: "merge" });
  }

  async function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const run = ++pickRun.current;
    // The item list is only readable inside this handler, so it is read before the first await.
    const walking = fromDrop(event.dataTransfer.items);
    setStep({ kind: "reading" });
    const picked = await walking;
    if (pickRun.current !== run) return;
    if (picked === null) setStep({ kind: "pick", problem: "Drop a folder, not files — single files go up from a project's Files page." });
    else await plan(picked, run);
  }

  async function bringIn(plan: ImportPlan, mode: Mode) {
    setStep({ kind: "working", plan, phase: { kind: "packing", read: 0, total: plan.bytes } });
    try {
      const disk = await api.get<Disk>("/api/disk");
      if (!fits(plan.bytes, disk.free_bytes)) {
        setStep({
          kind: "failed",
          plan,
          mode,
          message: `It won't fit — the folder is ${size(plan.bytes)} and Eggie has ${size(disk.free_bytes)} of room left. Make some space in the desktop app, then try again.`,
        });
        return;
      }
      await runImport(importApi, {
        id: plan.id,
        mode,
        bytes: plan.bytes,
        pack: (onRead) => packGzip(plan.entries, onRead),
        onPhase: (phase) => setStep({ kind: "working", plan, phase }),
      });
    } catch (error) {
      setStep({ kind: "failed", plan, mode, message: error instanceof Error ? error.message : "Something went wrong." });
      return;
    }
    await client.invalidateQueries({ queryKey: ["projects"] });
    setStep(PICK);
    onClose();
    navigate(`/p/${encodeURIComponent(plan.id)}`);
  }

  let body;
  switch (step.kind) {
    case "pick":
      body = (
        <>
          <div
            className={cx(s.dropZone, dragging && s.dropZoneActive)}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => void onDrop(event)}
          >
            <span className={s.dropIcon}>{FOLDER}</span>
            <p className={s.dropText}>Drop a folder here, or</p>
            <Button
              variant="primary"
              onClick={choose}
            >
              Choose a folder…
            </Button>
          </div>
          <p className={s.quiet}>The original stays exactly where it is. Repositories, dependency trees and caches are left out — your coding agent puts them back.</p>
          {step.problem && <Notice>{step.problem}</Notice>}
        </>
      );
      break;
    case "reading":
      body = (
        <>
          <div className={s.loading} role="status">
            <span className={s.spinner} aria-hidden="true" />
            <p>Reading the folder…</p>
          </div>
          <p className={s.quiet}>This can take a while, depending on how big the project is.</p>
          <div className={s.formActions}>
            <Button variant="quiet" onClick={reset}>Cancel</Button>
          </div>
        </>
      );
      break;
    case "review": {
      const { plan, mode } = step;
      const conflict = existing.includes(plan.id);
      body = (
        <>
          <dl className={s.summary}>
            <dt>Folder</dt>
            <dd>{plan.name}</dd>
            <dt>It'll be called</dt>
            <dd><strong>{plan.id}</strong></dd>
            <dt>Going in</dt>
            <dd>{plan.entries.length} {plan.entries.length === 1 ? "file" : "files"} · {size(plan.bytes)}</dd>
          </dl>
          {plan.skipped.length > 0 && <p className={s.quiet}>Left out: {plan.skipped.join(", ")}.</p>}
          {conflict && (
            <fieldset className={s.modes}>
              <legend className={s.modesTitle}>There's already a project called {plan.id}</legend>
              <label className={s.mode}>
                <input type="radio" name="mode" checked={mode === "merge"} onChange={() => setStep({ ...step, mode: "merge" })} />
                <span><strong>Merge into it</strong> — adds the new files and updates ones with the same name. Everything else stays put.</span>
              </label>
              <label className={s.mode}>
                <input type="radio" name="mode" checked={mode === "replace"} onChange={() => setStep({ ...step, mode: "replace" })} />
                <span><strong>Replace it</strong> — empties the project first, then copies your folder in.</span>
              </label>
              {mode === "replace" && (
                <Notice>
                  Replace deletes files. Everything in the existing project is gone for good — its files, its database data and its secrets, including work your coding agent did in there. There's no undo.
                </Notice>
              )}
            </fieldset>
          )}
          <div className={s.formActions}>
            <Button variant={mode === "replace" ? "danger" : "primary"} onClick={() => void bringIn(plan, mode)}>
              {mode === "replace" ? "Replace and bring it in" : "Bring it in"}
            </Button>
            <Button variant="quiet" onClick={reset}>Pick another</Button>
          </div>
        </>
      );
      break;
    }
    case "working":
      body = (
        <>
          <p>Carrying <strong>{step.plan.name}</strong> in. Your original folder isn't being touched.</p>
          <ProgressBar value={fraction(step.phase)} label={`${step.plan.name} import`} />
          <p className={s.quiet}>{phaseLine(step.phase)}</p>
        </>
      );
      break;
    case "failed":
      body = (
        <>
          <p className={s.error}>{step.message}</p>
          <div className={s.formActions}>
            <Button variant="primary" onClick={() => void bringIn(step.plan, step.mode)}>Try again</Button>
            <Button variant="quiet" onClick={reset}>Pick another</Button>
          </div>
        </>
      );
      break;
  }

  // Rendered only while open so a half-finished pick doesn't linger
  // underneath a closed dialog.
  return (
    <Modal open={open} onClose={close} title="Bring in a folder">
      <div className={s.form}>{open && body}</div>
      <input
        ref={picker}
        type="file"
        hidden
        {...{ webkitdirectory: "" }}
        onChange={(event) => {
          // Copied before the reset: clearing the value empties the live FileList.
          const picked = event.target.files ? fromFileList(event.target.files) : [];
          event.target.value = "";
          const run = opened.current;
          if (run !== pickRun.current) return;
          if (picked.length > 0) void plan(picked, run);
          else setStep(PICK);
        }}
      />
    </Modal>
  );
}
