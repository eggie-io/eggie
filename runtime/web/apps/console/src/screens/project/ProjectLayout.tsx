import { useState, useSyncExternalStore } from "react";
import { Outlet, useParams } from "react-router";
import { ApiError } from "../../api/client";
import { usePushJob, usePushTargets, useProject } from "../../projects/queries";
import { menuPush } from "../../push/push";
import { DeleteModal } from "./DeleteModal";
import { ProjectMenu, type ProjectDialog } from "./ProjectMenu";
import { PublicModal } from "./PublicModal";
import { PushModal } from "./PushModal";
import s from "./ProjectLayout.module.css";

function subscribeOnline(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

export function ProjectLayout() {
  const { id = "" } = useParams();
  const query = useProject(id);
  const [dialog, setDialog] = useState<ProjectDialog | null>(null);
  const close = () => setDialog(null);
  // The layout stays mounted across /p/:id changes; an open dialog must not carry over.
  const [dialogId, setDialogId] = useState(id);
  if (dialogId !== id) {
    setDialogId(id);
    setDialog(null);
  }
  // TanStack keeps stale data on a failed refetch; a project deleted elsewhere
  // must not keep its full menu.
  const gone = query.error instanceof ApiError && query.error.code === "project_not_found";
  const project = gone ? undefined : query.data;

  const targets = usePushTargets(id, dialog === "push");
  const pushJob = usePushJob(project);
  const browserOnline = useSyncExternalStore(subscribeOnline, () => navigator.onLine);
  const online = browserOnline && !(targets.error instanceof ApiError && targets.error.code === "cloud_unavailable");
  const progress = pushJob.data?.progress;
  const pushing = project?.job?.kind !== "push" ? null : progress && progress.total > 0 ? (progress.done / progress.total) * 100 : 0;
  const push = project ? menuPush(project, online, pushing) : null;

  return (
    <div className={s.layout}>
      <ProjectMenu id={id} project={project} push={push} onOpen={setDialog} />
      <div className={s.content}>
        <Outlet />
      </div>
      {project && (
        <>
          <DeleteModal id={project.id} open={dialog === "delete"} onClose={close} />
          <PublicModal project={project} open={dialog === "public"} onClose={close} />
          <PushModal project={project} open={dialog === "push"} onClose={close} />
        </>
      )}
    </div>
  );
}
