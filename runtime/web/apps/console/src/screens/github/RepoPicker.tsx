import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@eggie/ui";
import { invalidateGitHub, useCloneRepo, useRepos } from "../../github/github";
import { relativeTime } from "../../projects/format";
import { useJob } from "../../projects/queries";
import s from "./GitHubModal.module.css";

export function RepoPicker({ login, onDone }: { login: string; onDone: () => void }) {
  const repos = useRepos(true);
  const clone = useCloneRepo();
  const [started, setStarted] = useState<{ job_id: string; id: string } | null>(null);

  if (started) {
    return (
      <Cloning
        jobId={started.job_id}
        id={started.id}
        onDone={onDone}
        onRetry={() => setStarted(null)}
      />
    );
  }

  const all = repos.data?.pages.flatMap((page) => page.repos) ?? [];
  return (
    <>
      <p>Connected as @{login}. Pick a repository to make it a project:</p>
      {repos.isError && <p className={s.error}>{repos.error.message}</p>}
      {clone.error && <p className={s.error}>{clone.error.message}</p>}
      <ul className={s.repos}>
        {all.map((repo) => (
          <li key={repo.full_name}>
            <button
              className={s.repo}
              disabled={clone.isPending}
              onClick={() => clone.mutate(repo.full_name, { onSuccess: setStarted })}
            >
              <span className={s.repoName}>{repo.full_name}</span>
              {repo.private && <span className={s.badge}>Private</span>}
              {repo.updated_at !== null && (
                <span className={s.muted}>updated {relativeTime(repo.updated_at, Date.now())}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
      {repos.isLoading && <p className={s.muted}>Fetching your repositories…</p>}
      {repos.hasNextPage && (
        <Button variant="quiet" onClick={() => void repos.fetchNextPage()} disabled={repos.isFetchingNextPage}>
          Load more
        </Button>
      )}
    </>
  );
}

// The project exists only once the clone lands, so the page opens after
// the "cloning" phase, not on submit.
function Cloning({
  jobId,
  id,
  onDone,
  onRetry,
}: {
  jobId: string;
  id: string;
  onDone: () => void;
  onRetry: () => void;
}) {
  const job = useJob(jobId).data;
  const navigate = useNavigate();
  const client = useQueryClient();
  const cloned = job !== undefined && job.state !== "failed" && (job.phase !== "cloning" || job.state === "done");
  const failed = job?.state === "failed";
  // onDone/navigate get a new identity on every parent render, which would
  // otherwise re-run this effect and push a second history entry.
  const fired = useRef(false);
  useEffect(() => {
    if (cloned && !fired.current) {
      fired.current = true;
      onDone();
      navigate(`/p/${encodeURIComponent(id)}`);
    }
  }, [cloned, id, navigate, onDone]);
  // A failed clone may have moved the connection to needs_reconnect
  // server-side (e.g. a revoked token); refresh once so the modal reflects it.
  const invalidated = useRef(false);
  useEffect(() => {
    if (failed && !invalidated.current) {
      invalidated.current = true;
      void invalidateGitHub(client);
    }
  }, [failed, client]);
  if (job?.state === "failed") {
    return (
      <>
        <p className={s.error}>Couldn't download it: {job.detail}</p>
        <Button variant="quiet" onClick={onRetry}>Pick another</Button>
      </>
    );
  }
  return <p>Downloading {id} from GitHub…</p>;
}
