import { useState } from "react";
import { Outlet, useParams } from "react-router";
import { ApiError } from "../../api/client";
import { useProject } from "../../projects/queries";
import { AnalyzeModal } from "./AnalyzeModal";
import { DeleteModal } from "./DeleteModal";
import { ProjectMenu, type ProjectDialog } from "./ProjectMenu";
import { PublicModal } from "./PublicModal";
import s from "./ProjectLayout.module.css";

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

  return (
    <div className={s.layout}>
      <ProjectMenu id={id} project={project} onOpen={setDialog} />
      <div className={s.content}>
        <Outlet />
      </div>
      {project && (
        <>
          <AnalyzeModal id={project.id} open={dialog === "analyze"} onClose={close} />
          <DeleteModal id={project.id} open={dialog === "delete"} onClose={close} />
          <PublicModal project={project} open={dialog === "public"} onClose={close} />
        </>
      )}
    </div>
  );
}
