import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Button, Icon } from "@eggie/ui";
import { UpdatingOverlay } from "../screens/UpdatingOverlay";
import { latchStart, refusedStart, updateView, useRuntimeUpdate, useStartUpdate } from "../update/update";
import s from "./Shell.module.css";

export function UpdateButton() {
  const [open, setOpen] = useState(false);
  const [startedFrom, setStartedFrom] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const start = useStartUpdate();
  const { data } = useRuntimeUpdate(startedFrom, start.isPending);
  const view = updateView(data, startedFrom);
  const client = useQueryClient();

  useEffect(() => {
    setStartedFrom((was) => latchStart(view, was, data?.installed ?? null));
  }, [view, data?.installed]);

  useEffect(() => {
    if (view.kind === "done") window.location.reload();
  }, [view.kind]);

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  if (view.kind === "updating") return <UpdatingOverlay />;
  if (view.kind !== "offer") return null;

  const begin = () => {
    setStartedFrom(view.installed);
    start.mutate(undefined, {
      onError: (error) => {
        if (refusedStart(error)) setStartedFrom(null);
        void client.invalidateQueries({ queryKey: ["runtime-update"] });
      },
    });
  };

  return (
    <div className={s.account} ref={root}>
      <button
        type="button"
        className={s.updatePill}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
      >
        <Icon name="upload" />
        <span>Update available</span>
      </button>
      <div id={panelId} className={s.updatePanel} hidden={!open}>
        <h2 className={s.updateTitle}>Eggie {view.available} is available</h2>
        {view.installed && <p className={s.updateLine}>You have {view.installed}.</p>}
        <p className={s.updateLine}>Eggie restarts for about a minute. Your projects keep running.</p>
        {view.problem && <p className={s.menuError} role="alert">The last update didn't finish: {view.problem}</p>}
        {start.error && <p className={s.menuError} role="alert">{start.error.message}</p>}
        <Button variant="primary" size="sm" onClick={begin} disabled={start.isPending}>
          {view.problem ? "Try again" : "Update now"}
        </Button>
      </div>
    </div>
  );
}
