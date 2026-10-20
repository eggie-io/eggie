import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Notice, TextField } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { invalidateGitHub, invalidateRepos, useCloneRepo, useRepos, type Repo } from "../../github/github";
import { repoRow } from "../../github/view";
import { relativeTime } from "../../projects/format";
import type { Job } from "../../projects/types";
import { slugify } from "../../projects/slugify";
import s from "./GitHubModal.module.css";

export type Started = { job_id: string; id: string };

// id stays null until the user types, so the field follows the row's
// suggestion as the list refetches.
type Naming = { repo: string; id: string | null; error: string | null };

export function RepoPicker({
  login,
  onDone,
  onStarted,
}: {
  login: string;
  onDone: () => void;
  onStarted: (started: Started) => void;
}) {
  const repos = useRepos(true);
  const clone = useCloneRepo();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [naming, setNaming] = useState<Naming | null>(null);

  const start = (repo: Repo, id?: string) => {
    // A direct click leaves no name field open to hide its error.
    if (id === undefined) setNaming(null);
    clone.mutate(
      { repo: repo.full_name, id },
      {
        onSuccess: onStarted,
        onError: (error) => {
          // Another project took the name after this list was fetched.
          if (error instanceof ApiError && error.code === "project_exists") {
            setNaming({ repo: repo.full_name, id: id ?? null, error: error.message });
            void invalidateRepos(client);
          } else if (id !== undefined) {
            // The generic error line is hidden while the name field is open.
            setNaming({ repo: repo.full_name, id, error: error.message });
          }
        },
      },
    );
  };
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
      {repos.isError && <Notice tone="danger">{repos.error.message}</Notice>}
      {clone.error && !naming && <Notice tone="danger">{clone.error.message}</Notice>}
      <ul className={s.repos}>
        {all.map((repo) => {
          const row = repoRow(repo);
          const name = naming?.repo === repo.full_name ? naming.id ?? repo.suggested_id ?? "" : "";
          const details = (
            <>
              <span className={s.repoName}>{repo.full_name}</span>
              {repo.private && <Badge icon="lock">Private</Badge>}
              {row.kind === "linked" && <Badge tone="success">Already a project</Badge>}
              {row.kind === "nameTaken" && <Badge tone="warning">Name taken</Badge>}
              {repo.updated_at !== null && (
                <span className={s.updated}>updated {relativeTime(repo.updated_at, Date.now())}</span>
              )}
            </>
          );
          return (
            <li key={repo.full_name} className={s.item}>
              {row.kind === "linked" ? (
                <div className={s.repo}>
                  {details}
                  <Button variant="ghost" size="sm" iconRight="chevron-right" className={s.open} onClick={() => open(row.projectId)}>
                    Open
                  </Button>
                </div>
              ) : (
                <button
                  className={s.repo}
                  disabled={clone.isPending}
                  onClick={() =>
                    row.kind === "nameTaken"
                      ? setNaming({ repo: repo.full_name, id: null, error: null })
                      : start(repo)
                  }
                >
                  {details}
                </button>
              )}
              {naming?.repo === repo.full_name && row.kind !== "linked" && (
                <form
                  className={s.rename}
                  onSubmit={(event) => {
                    event.preventDefault();
                    start(repo, name);
                  }}
                >
                  <TextField
                    label="Name the new project"
                    value={name}
                    onChange={(id) => setNaming({ ...naming, id, error: null })}
                    autoFocus
                    error={naming.error ?? undefined}
                    hint={slugify(name) ? <>It'll be called <strong>{slugify(name)}</strong></> : undefined}
                  />
                  <div className={s.actions}>
                    <Button variant="ghost" size="sm" onClick={cancelNaming} disabled={clone.isPending}>Cancel</Button>
                    <Button variant="primary" size="sm" type="submit" disabled={clone.isPending || !slugify(name)}>
                      Import
                    </Button>
                  </div>
                </form>
              )}
            </li>
          );
        })}
      </ul>
      {repos.isLoading && <p>Fetching your repositories…</p>}
      {repos.hasNextPage && (
        <Button
          variant="ghost"
          size="sm"
          className={s.more}
          onClick={() => void repos.fetchNextPage()}
          disabled={repos.isFetchingNextPage}
        >
          Load more
        </Button>
      )}
    </>
  );
}

// The project exists only once the clone lands, so the page opens after
// the "cloning" phase, not on submit.
export function Cloning({ id, job, onDone }: { id: string; job: Job | undefined; onDone: () => void }) {
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
  if (job?.state === "failed") return <Notice tone="danger">Couldn't download it: {job.detail}</Notice>;
  return <p>Downloading {id} from GitHub…</p>;
}
