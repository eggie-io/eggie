import { useRef, useState } from "react";
import { Egg, ProgressBar, StateBadge } from "@eggie/ui";
import { Elapsed } from "../../components/Elapsed";
import { phaseCaption } from "../../projects/copy";
import { useJob } from "../../projects/queries";
import type { ActiveJob, Project } from "../../projects/types";
import s from "./ProjectPage.module.css";

export function StartingBody({ project, job }: { project: Project; job: ActiveJob }) {
  const live = useJob(job.id);
  // Once the job query has failed, the 3 s project poll is the only thing
  // still moving; let its phase win instead of freezing on the last one seen.
  const phase = live.isError ? job.phase : (live.data?.phase ?? job.phase);

  // The API flips first_run before the job settles into "checking", which
  // would otherwise swap this paragraph mid-start. Latch it per job.
  const jobId = useRef(job.id);
  const [firstRun, setFirstRun] = useState(project.first_run);
  if (jobId.current !== job.id) {
    jobId.current = job.id;
    setFirstRun(project.first_run);
  }

  const caption = phaseCaption(phase, firstRun);
  return (
    <div className={s.center}>
      <Egg size={88} bob />
      <StateBadge state="starting" />
      <h1 className={s.big}>Heating the pan for {project.id}</h1>
      <p className={s.lead}>
        {firstRun
          ? "The first start always takes the longest — it's fetching everything your app needs to run. A few minutes, once. After that it's about ten seconds."
          : "This usually takes about ten seconds."}
      </p>
      <div className={s.progress}>
        <ProgressBar label={caption} />
        <div className={s.caption}>
          <span>{caption}</span>
          <span><Elapsed startedAt={job.started_at} /> so far</span>
        </div>
      </div>
      <p className={s.footnote}>
        You can wander off — this keeps going with the page closed, and the address turns on by itself when it's ready.
      </p>
    </div>
  );
}
