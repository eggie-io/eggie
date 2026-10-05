# Eggie web UI — part C: project screens

Date: 2026-09-22
Builds on: `2026-09-21-web-ui-agent-prerequisites-design.md` (§1 decisions, §8 "C"),
`2026-09-22-web-ui-shell-and-kit-design.md` (shell, kit, client, mocks).
Board frames: 01–06, 11, 12, 16 (`docs/design/`).

## 1. Outcome

A signed-in user manages projects from `localhost:39080`: sees every project and
its state, creates an empty one, adopts a folder their coding agent made, starts,
stops and restarts, opens its addresses, reads why it broke and gets a prompt that
fixes it, asks for an analysis prompt, and deletes it. Files (part D) are not here.

Decisions:

- **New project** is a name modal (the board stops at the button).
- **Files** tile is disabled with "Soon"; **Public address** is disabled with
  "Needs an account", as drawn. Part D switches Files on.
- No agent changes. What the agent can't answer is cut from the board:
  - "Cooking for 2 hours" — no start time in the payload; the subtitle keeps
    only "N addresses open".
  - Address labels "Its API" / "Database viewer" are examples; real rows are
    "The app itself" for the primary `web[]` entry and the compose service name
    for the rest.
  - The new-project modal previews the project's name, not its full address —
    the agent's domain is not readable before a project exists.
- Sync marker stays hidden (A §1).
- Two of B's review notes are closed here: 403 gets its own screen; PromptCard
  says so when the clipboard refuses.

## 2. Routes and state

`/` — list. `/p/:id` — project page; its body switches on the project's view
kind, so every state of one project shares one URL. `/kit` stays. An unknown id
(404 `project_not_found`) shows "No project called X" with a link to the list.

### `projectView(project)`

One pure function in `apps/console/src/projects/view.ts`, used by the row and
the page. First match wins:

| # | Condition | `kind` | Badge (`ProjectState`) |
|---|---|---|---|
| 1 | `job` running, `kind` `up` or `restart` | `starting` | `starting` |
| 2 | `job` running, `kind` `down` | `stopping` | `stopped` |
| 3 | `problem.code == "folder_missing"` | `gone` | `wrong` |
| 4 | `empty` | `waiting` | `stopped` |
| 5 | `problem` set, or `status` `failed_to_start` / `crash_looping` | `wrong` | `wrong` |
| 6 | `status == "started_ok"` | `running` | `running` |
| 7 | otherwise | `stopped` | `stopped` |

For `wrong`, `cause` is `problem.code` when set, else the status. Causes:
`bound_to_loopback`, `service_unreachable`, `crash_looping`, `failed_to_start`,
`unreadable` (`invalid_compose`, `invalid_project`; `compose_missing` without
`empty` also lands here). An unknown code maps to `service_unreachable`'s copy
with the agent's own `problem.message` shown under it.

### Refresh

- List and project page: `GET /api/projects` / `GET /api/projects/{id}` every
  3 s while any shown project has a running `job`, 15 s otherwise (discovered
  folders appear without a reload). No polling while the tab is hidden
  (TanStack's default).
- Starting: `GET /api/jobs/{job.id}` every 1 s for `phase`. When `state` leaves
  `running`, the project query is invalidated and the page follows the new kind.
  A 404 `job_not_found` (agent restarted) also just invalidates.
- Elapsed = `now − started_at`, floored at 0 (the VM clock may run ahead),
  shown `m:ss`, or `h:mm:ss` from an hour.

### Actions

Start `POST up`, Stop `POST down`, Restart / Try again `POST restart`; each then
invalidates the project and the list. 409 `project_busy` shows an inline Notice
"It's already busy — give it a moment." Any other failure shows the agent's
message in a Notice. Stop has no confirmation.

## 3. Screens

Copy in quotes is final; board copy not repeated here is used verbatim.

### List (frames 02, 16) and empty (01)

- "Your projects" + subtitle "<Total> on the go · <n> of them cooking" — numbers
  one–nine in words, digits after; "cooking" counts `running`; with none
  running: "<Total> on the go". "New project" button at the right.
- Row: name, primary address (link when running, grey text otherwise, absent
  for `waiting`/`gone`/`wrong`), badge, and by kind:
  running — Open (new tab, `noopener`) + Stop; stopped — Start; starting —
  elapsed; stopping — "Putting it away…"; wrong — one-line cause + "Take a look"
  (paprika ring as drawn); waiting — "Waiting for your coding agent" + Open
  page; gone — "Its folder has gone missing" + Take a look. The name opens
  `/p/:id`.
- No projects and no discovered folders: frame 01 as drawn. "From GitHub" card
  with "Soon" and a disabled "Not yet". The desktop line is plain text:
  "Already have a folder on your computer? The desktop app carries it in for you."
- Footer: "These addresses only work on this computer. Nothing is out on the internet."
- At ≥1040 px rows use the frame 16 grid (`1fr 200px 200px`).

### Discovered band (03, 16)

Above the list when `discovered` is non-empty. Heading "A folder turned up" /
"<Two…> folders turned up", sub "Your coding agent made these. Eggie hasn't met
them yet." Per folder, "Turned up <relative time>" from `seen_at`, then:

- adoptable — "· knows how to start itself" + "Adopt it" → `POST
  /projects/{name}/adopt` → `/p/:id`. 409 `project_exists` / `not_adoptable`
  and 404 `folder_not_found` show the agent's message and refetch.
- `compose_missing` — dashed card, board copy, "What Eggie looks for"
  Collapsible with `~/projects/<name>\n  docker-compose.yml  — missing`,
  disabled "Can't adopt".
- `bad_name` — dashed card, "Its name has characters an address can't use — ask
  your coding agent to rename the folder to <slug>." disabled "Can't adopt".

### New project modal

Title "Name your project". One text field; under it "It'll be called
**<slug>**" once the slug is non-empty. "Make it" is disabled while the slug is
empty or a request is in flight; "Keep it for later" closes. `POST /projects
{id: <raw input>}` → `/p/<returned id>`. 409 `project_exists` → under the field
"There's already a project called <slug>." 422 → the agent's message.
`slugify` mirrors the agent's `_slug`: lower-case, trim, runs of anything
outside `[a-z0-9-]` become `-`, leading/trailing `-` stripped.

### Project page

Common: "‹ All projects", egg + name + badge, then the body by kind. Tile grid
(running, stopped, wrong, waiting): Analyze, Files (disabled, "Soon"), Public
address (disabled, "Needs an account"), Delete. `gone` has none.

- **running** (04) — "<n> address(es) open"; Open in browser (primary url),
  Stop, Restart. "Where to find it": one row per `web[]` entry with Copy.
- **starting** (05) — "Heating the pan for <id>"; ProgressBar (indeterminate)
  with caption by phase and "<elapsed> so far":

  | phase | caption |
  |---|---|
  | `preparing` | "Getting the pan out" |
  | `starting`, `first_run` | "Fetching the bits it needs" |
  | `starting` | "Starting it up" |
  | `checking` | "Checking it answers" |
  | other | "Working on it" |

  The first-start paragraph only when `first_run`; otherwise "This usually takes
  about ten seconds." The "wander off" line always.
- **stopping** — "Putting <id> away…" and elapsed.
- **wrong** (06) — heading + body by cause, "Try again" (restart), "Get a
  prompt that fixes it" (Modal with a PromptCard of the cause's prompt),
  "Nothing is lost. Your files are exactly where you left them.", "The raw
  details" Collapsible that fetches `GET /projects/{id}/logs` (no `follow`) the
  first time it opens and shows it in a scrollable `<pre>`; 409
  `logs_unavailable` → "There aren't any logs to show yet." "Was going to be"
  strip with the primary url when there is one.

  | cause | heading | body |
  |---|---|---|
  | `bound_to_loopback` | "<id> started, but it isn't answering" | board copy |
  | `service_unreachable` | "<id> started, but nothing answers at its address" | "Its little machines are up, but nothing replies where Eggie sends visitors. It may be listening on a different port, or it fell over after starting." |
  | `crash_looping` | "<id> keeps falling over" | "It starts, trips, and starts again. The raw details below usually say why — your coding agent can read them." |
  | `failed_to_start` | "<id> couldn't start" | "Docker refused to start it. The raw details below say what it tripped on." |
  | `unreadable` | "Eggie can't read <id>'s start-up recipe" | "The docker-compose.yml is there, but Eggie can't make sense of it." + `problem.message` |

  Each cause has a fix prompt in `projects/prompts.ts`, written to a coding
  agent, naming the project folder `~/projects/<id>` and asking it to fix the
  cause, keep the change minimal, and say in plain words what it changed.
- **waiting** — "Nothing to cook yet", "<id> is an empty folder at
  ~/projects/<id>. Your coding agent fills it in; once there's a
  docker-compose.yml, Start appears here." + PromptCard asking the agent to
  build the project there and add a `docker-compose.yml`.
- **gone** — "The folder for <id> has gone missing", "Eggie still remembers
  it, but ~/projects/<id> isn't there any more." Only "Forget it" → plain
  `DELETE /projects/{id}` → list.
- **stopped** — Start; address rows greyed without Copy.

### Analyze (11)

Modal "Ask for a once-over", board subtitle, PromptCard with the board's prompt
verbatim, footer "Close" + "Nothing was sent anywhere. This is just words on
your clipboard."

### Delete (12)

Modal "Throw out <id>?" → on open `GET /projects/{id}/delete-preview`; while
pending "Counting what's in there…". Rows, each only when non-empty:

- "Every file in the project" — "<n> files · <size>" (B, KB, MB, GB; one
  decimal under 10).
- "The little machines that run it" — container names, then "stopped & removed".
- "Its stored data" — volume names.

Board warning and "Other projects aren't touched." "Yes, throw it out" →
`DELETE /projects/{id}?purge=true` → list. Result `stopped: false` → the list
shows Notice "<id> is deleted. Some of its little machines may still be
running — restarting Eggie from the desktop app clears them." (never worded as
a failure). 409 `project_busy` → inline, modal stays. The preview can undercount
folders the agent can't read; the count is shown as is.

### Kit changes

- PromptCard: when `clipboard.writeText` rejects, the text is selected and the
  hint becomes "Couldn't copy — the text is selected, press Ctrl+C (⌘C on a Mac)."
- A `TextField` component (label, value, hint, error) for the modal; kit gallery
  shows it.

### Wrong host

`boot` returns `wrongHost` on 403 `forbidden_host` / `forbidden_origin` from
`/api/health` or `/api/session`: screen "This page was opened from an address
Eggie doesn't recognise" / "Open it as http://localhost:39080 — the desktop
app's Open Eggie button does that for you." with "Try again". Mid-use 403s show
the agent's message in a Notice.

## 4. Mocks

`mocks/` becomes a stateful fake agent (module-level state, reset per page load):

- `ok`: recipe-box (running, three `web[]` entries: web, api, studio),
  weekend-shop (stopped), tiny-crm (first-run `up` job), photo-sorter
  (`bound_to_loopback`), notes-app (empty), old-blog (`folder_missing`);
  discovered invoice-helper (adoptable), spice-rack (`compose_missing`),
  `Tax Stuff` (`bad_name`).
- Jobs step `preparing → starting → checking` (down: `preparing → stopping`)
  every 2 s, 6 s per step when `first_run`, then set status and clear `job`.
- Create, adopt, delete-preview, delete (`stopped: false` for photo-sorter),
  project logs, 409 `project_busy` on a second action while a job runs,
  404 for unknown ids, `project_exists`.
- New scenarios: `empty` (no projects, no discovered), `wrong-host` (403
  `forbidden_host` on health).

## 5. Testing

Vitest, pure modules only; no component rendering. Each test states the bug:

- `projectView`: one case per row of the table, plus the precedence cases —
  running job with a stale problem is `starting`; `empty` with
  `compose_missing` is `waiting`; unknown code is `wrong`.
- `slugify` against `tests/fixtures/slugify-cases.json` (`[{name, slug}]`),
  also read by a pytest that runs the agent's `_slug` over it — the preview
  can't promise an id the agent won't create.
- `elapsed`: negative → `0:00`, `59:59`, `1:00:00`.
- `deleteSummary`: empty lists dropped, size units at their boundaries.
- `boot`: 403 `forbidden_host` → `wrongHost`.
- Subtitle words: 0, 1, 9, 10, none running.

The browser walk of every scenario and both widths stays a human check.

## 6. Branch

`feature/web-projects` from `feature/web-shell` (C needs B; neither is merged).
