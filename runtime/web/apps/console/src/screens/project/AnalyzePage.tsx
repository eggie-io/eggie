import { useState } from "react";
import { useParams } from "react-router";
import { Notice, SegmentedControl } from "@eggie/ui";
import { atSentenceStart } from "../../agents/current";
import { useCurrentAgent } from "../../agents/queries";
import { ApiError } from "../../api/client";
import { ANALYZE_GETS, analyzePrompt, type Focus } from "../../projects/prompts";
import { useProject } from "../../projects/queries";
import type { Project } from "../../projects/types";
import { projectView, type ProjectView } from "../../projects/view";
import { HandoffCard } from "./HandoffCard";
import { TitleRow } from "./TitleRow";
import s from "./ProjectPage.module.css";

const FOCUSES: { value: Focus; label: string }[] = [
  { value: "full", label: "Full once-over" },
  { value: "start", label: "Why won't it start?" },
  { value: "share", label: "Safe to share?" },
];

const defaultFocus = (view: ProjectView): Focus => (view.kind === "wrong" || view.kind === "setup" ? "start" : "full");

// A named fault keeps Docker's message out of `detail`; an empty set-up folder's null is
// deliberate (that message is Eggie's, not Docker's).
function startDetail(project: Project, view: ProjectView): string | null {
  if (view.kind === "setup") return view.detail;
  if (view.kind === "wrong") return view.detail ?? project.problem?.message ?? null;
  return null;
}

export function AnalyzePage() {
  const { id = "" } = useParams();
  const query = useProject(id);
  const agent = useCurrentAgent();
  const view = query.data ? projectView(query.data) : null;
  // The default is taken once per project, so a project that starts running while
  // the page is open doesn't switch the question under the reader.
  const [picked, setPicked] = useState<{ id: string; focus: Focus } | null>(null);
  if (view && picked?.id !== id) setPicked({ id, focus: defaultFocus(view) });

  if (query.error instanceof ApiError && query.error.code === "project_not_found") {
    return (
      <section className={s.page}>
        <TitleRow title={`No project called ${id}`} />
      </section>
    );
  }

  if (query.data === undefined || view === null || picked?.id !== id) {
    return (
      <section className={s.page}>
        <TitleRow title="Analyze" />
        {query.isError ? <Notice tone="danger">{query.error.message}</Notice> : <p className={s.muted}>Checking the kitchen…</p>}
      </section>
    );
  }

  const project = query.data;
  const focus = picked.focus;
  const detail = focus === "start" ? startDetail(project, view) : null;
  const name = atSentenceStart(agent.name);

  return (
    <section className={s.page}>
      <TitleRow
        title="Analyze"
        sub={<span className={s.lead}>Get a plain-language read on this project. Eggie doesn't look at your code. {name} reads it and answers there. Nothing gets changed.</span>}
      />
      <div className={s.ask}>
        <span className={s.askLabel}>What do you want to know?</span>
        <SegmentedControl label="Analyze focus" options={FOCUSES} value={focus} onChange={(next) => setPicked({ id, focus: next })} />
      </div>
      <HandoffCard
        title={`This happens in ${agent.name}`}
        agent={agent}
        steps={{
          prompt: analyzePrompt(focus, project.id, detail),
          extra: detail ?? undefined,
          copyHint: `It tells ${agent.name} not to change any files.`,
          pasteHint: `Takes a minute or two. Read the report in ${agent.name}, not here.`,
          last: (
            <>
              <span className={s.stepTitle}>What you'll get back</span>
              <ul className={s.gets}>
                {ANALYZE_GETS[focus].map((item) => <li key={item} className={s.get}>{item}</li>)}
              </ul>
            </>
          ),
        }}
      />
    </section>
  );
}
