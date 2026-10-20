import { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Notice, TextField, cx } from "@eggie/ui";
import { useCurrentAgent } from "../../agents/queries";
import { ApiError } from "../../api/client";
import { invalidateGitHub, invalidateRepos, useCloneRepo, useRepos, type Repo } from "../../github/github";
import { filterRepos, repoRow, shouldLoadMore } from "../../github/view";
import { relativeTime } from "../../projects/format";
import type { Job } from "../../projects/types";
import { slugify } from "../../projects/slugify";
import { WhatNext } from "../list/WhatNext";
import s from "./GitHubModal.module.css";

export type Started = { job_id: string; id: string };

// id stays null until the user types, so the field follows the row's
// suggestion as the list refetches.
type Naming = { repo: string; id: string | null; error: string | null };

// Lives in GitHubModal so the footer's Bring it in can start the selected repo.
export function useRepoPick(onStarted: (started: Started) => void) {
  const clone = useCloneRepo();
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [naming, setNaming] = useState<Naming | null>(null);
  const renameForm = useId();

  const start = (repo: string, id?: string) => {
    // A start without a name leaves no name field open to hide its error.
    if (id === undefined) setNaming(null);
    clone.mutate(
      { repo, id },
      {
        onSuccess: onStarted,
        onError: (error) => {
          // Another project took the name after this list was fetched.
          if (error instanceof ApiError && error.code === "project_exists") {
            setNaming({ repo, id: id ?? null, error: error.message });
            void invalidateRepos(client);
          } else if (id !== undefined) {
            // The generic error line is hidden while the name field is open.
            setNaming({ repo, id, error: error.message });
          }
        },
      },
    );
  };
  const select = (repo: Repo) => {
    if (selected === repo.full_name) return;
    clone.reset();
    setSelected(repo.full_name);
    setNaming(repoRow(repo).kind === "nameTaken" ? { repo: repo.full_name, id: null, error: null } : null);
  };
  const clear = () => {
    setSelected(null);
    setNaming(null);
    clone.reset();
  };
  // With the name field open, Bring it in submits it so the typed name is used.
  const bringIn = naming ? { type: "submit" as const, form: renameForm } : { onClick: () => selected && start(selected) };

  return { clone, selected, naming, setNaming, renameForm, start, select, clear, bringIn };
}

export type RepoPick = ReturnType<typeof useRepoPick>;

export function RepoPicker({ login, pick, onDone }: { login: string; pick: RepoPick; onDone: () => void }) {
  const repos = useRepos(true);
  const navigate = useNavigate();
  const agent = useCurrentAgent({ poll: false });
  const [query, setQuery] = useState("");
  const { clone, naming, selected } = pick;

  const { hasNextPage, isFetching, isError, fetchNextPage } = repos;
  useEffect(() => {
    // An error would refetch the same page in a loop; Load more stays as the retry.
    if (!isError && shouldLoadMore(query, hasNextPage, isFetching)) void fetchNextPage();
  }, [query, hasNextPage, isFetching, isError, fetchNextPage]);

  const open = (projectId: string) => {
    onDone();
    navigate(`/p/${encodeURIComponent(projectId)}`);
  };

  const all = repos.data?.pages.flatMap((page) => page.repos) ?? [];
  const shown = filterRepos(all, query);
  const searching = query.trim() !== "";
  const search = (value: string) => {
    setQuery(value);
    // Bring it in must never start a repo the list no longer shows.
    if (selected && !filterRepos(all, value).some((repo) => repo.full_name === selected)) pick.clear();
  };

  return (
    <>
      <p>Connected as @{login}. Pick a repository to make it a project:</p>
      <TextField label="Search" icon="search" value={query} onChange={search} autoFocus />
      {repos.isError && <Notice tone="danger">{repos.error.message}</Notice>}
      {clone.error && !naming && <Notice tone="danger">{clone.error.message}</Notice>}
      <ul className={s.repos}>
        {shown.map((repo) => {
          const row = repoRow(repo);
          const isSelected = row.kind !== "linked" && selected === repo.full_name;
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
                  className={cx(s.repo, isSelected && s.selected)}
                  aria-pressed={isSelected}
                  disabled={clone.isPending}
                  onClick={() => pick.select(repo)}
                >
                  {details}
                </button>
              )}
              {isSelected && naming?.repo === repo.full_name && (
                <form
                  id={pick.renameForm}
                  className={s.rename}
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (slugify(name) && !clone.isPending) pick.start(repo.full_name, name);
                  }}
                >
                  <TextField
                    label="Name the new project"
                    value={name}
                    onChange={(id) => pick.setNaming({ ...naming, id, error: null })}
                    autoFocus
                    error={naming.error ?? undefined}
                    hint={slugify(name) ? <>It'll be called <strong>{slugify(name)}</strong></> : undefined}
                  />
                </form>
              )}
            </li>
          );
        })}
      </ul>
      {repos.isLoading && <p>Fetching your repositories…</p>}
      {searching && shown.length === 0 && !repos.isLoading && (
        <p>{isFetching ? "Looking through your repositories…" : "No repositories match"}</p>
      )}
      {!searching && hasNextPage && (
        <Button
          variant="ghost"
          size="sm"
          className={s.more}
          onClick={() => void fetchNextPage()}
          disabled={repos.isFetchingNextPage}
        >
          Load more
        </Button>
      )}
      {selected && (
        <WhatNext
          agent={agent.name}
          first="Copies the code to this computer"
          middle="Sets it up to run in Eggie. Most projects need this once, so it's not a sign anything's broken."
        />
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
