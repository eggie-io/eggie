import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, TextField } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { invalidateGitHub, invalidateRepos, useCloneRepo, useRepos, type Repo } from "../../github/github";
import { repoRow } from "../../github/view";
import { relativeTime } from "../../projects/format";
import { useJob } from "../../projects/queries";
import { slugify } from "../../projects/slugify";
import s from "./GitHubModal.module.css";

type Naming = { repo: string; id: string; error: string | null };

export function RepoPicker({ login, onDone }: { login: string; onDone: () => void }) {
  const repos = useRepos(true);
  const clone = useCloneRepo();
  const navigate = useNavigate();
  const [started, setStarted] = useState<{ job_id: string; id: string } | null>(null);
  const [naming, setNaming] = useState<Naming | null>(null);

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

  const start = (repo: Repo, id?: string) =>
    clone.mutate(
      { repo: repo.full_name, id },
      {
        onSuccess: setStarted,
        onError: (error) => {
          // Another project took the name after this list was fetched.
          if (error instanceof ApiError && error.code === "project_exists") {
            setNaming({ repo: repo.full_name, id: id ?? repo.suggested_id ?? "", error: error.message });
          }
        },
      },
    );
  const open = (projectId: string) => {
    onDone();
    navigate(`/p/${encodeURIComponent(projectId)}`);
  };
  const cancelNaming = () => {
    setNaming(null);
    clone.reset();
  };

  const all = repos.data?.pages.flatMap((page) => page.repos) ?? [];
  return (
    <>
      <p>Connected as @{login}. Pick a repository to make it a project:</p>
      {repos.isError && <p className={s.error}>{repos.error.message}</p>}
      {clone.error && !naming && <p className={s.error}>{clone.error.message}</p>}
      <ul className={s.repos}>
        {all.map((repo) => {
          const row = repoRow(repo);
          const details = (
            <>
              <span className={s.repoName}>{repo.full_name}</span>
              {repo.private && <span className={s.badge}>Private</span>}
              {row.kind === "linked" && <span className={s.badge}>Already a project</span>}
              {row.kind === "nameTaken" && <span className={s.badge}>Name taken</span>}
              {repo.updated_at !== null && (
                <span className={s.muted}>updated {relativeTime(repo.updated_at, Date.now())}</span>
              )}
            </>
          );
          return (
            <li key={repo.full_name}>
              {row.kind === "linked" ? (
                <div className={s.repo}>
                  {details}
                  <Button variant="quiet" onClick={() => open(row.projectId)}>Open</Button>
                </div>
              ) : (
                <button
                  className={s.repo}
                  disabled={clone.isPending}
                  onClick={() =>
                    row.kind === "nameTaken"
                      ? setNaming({ repo: repo.full_name, id: row.suggestion, error: null })
                      : start(repo)
                  }
                >
                  {details}
                </button>
              )}
              {naming?.repo === repo.full_name && (
                <form
                  className={s.rename}
                  onSubmit={(event) => {
                    event.preventDefault();
                    start(repo, naming.id);
                  }}
                >
                  <TextField
                    label="Name the new project"
                    value={naming.id}
                    onChange={(id) => setNaming({ ...naming, id, error: null })}
                    autoFocus
                    error={naming.error ?? undefined}
                    hint={slugify(naming.id) ? <>It'll be called <strong>{slugify(naming.id)}</strong></> : undefined}
                  />
                  <div className={s.actions}>
                    <Button variant="primary" type="submit" disabled={clone.isPending || !slugify(naming.id)}>
                      Import
                    </Button>
                    <Button variant="quiet" onClick={cancelNaming}>Cancel</Button>
                  </div>
                </form>
              )}
            </li>
          );
        })}
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
      void invalidateRepos(client);
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
