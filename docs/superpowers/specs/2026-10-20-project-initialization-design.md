# Project initialization: hand-off to the coding agent

Issue eggie-resources#72. Design: claude.ai/design project `5a77e605…`, file
`Eggie Host + Console v3.dc.html`, section "Turn 8 · handing work to the coding agent" (frames 8a–8f).

## Why

A tester made a project and waited for a "Start" button. Nothing said the coding agent builds it.
New project, set-up after a clone/import, and Analyze are all work Eggie can't do, so they share
one pattern: the dialog says who does what before anything happens; the project page then shows a
three-step card — copy the prompt, paste it into the agent, the agent starts it with `eggie up`.

## Goals

- Every create path (New project, From GitHub, From a folder) says up front what Eggie does and
  what the agent does.
- A project that has never run here is never shown as a red error. A new empty project shows
  **Waiting for agent**; a clone/import that doesn't start shows **Needs set-up** with a prompt
  carrying Docker's message.
- Analyze becomes a page with three questions.

## Non-goals

- No starter template project (user: rejected).
- No folder watcher and no auto-start in the API. The agent runs `eggie up` (its instructions
  already say so); the console only reflects the new state on its normal poll.
- No Changes/rollback, GitHub sync status (no API, as in the console redesign).
- No change to the host or to `API_VERSION` (additive fields only).

## API (runtime/eggie_api)

Built on `main` (PR #92, push to kitchen, is not merged; whichever lands second rebases — see
"After #92" below). New `projects` columns (migration `_v7_…`, no backfill — no users yet):

- `origin TEXT NOT NULL DEFAULT 'new'` — `new` | `github` | `folder`. Set by:
  `POST /projects` (new optional body field `origin`, only `new`/`folder` accepted; the console's
  folder import sends `folder`; CLI `eggie new` sends nothing → `new`), clone → `github`,
  adopt of a discovered folder → `folder`.
- `ran_ok_at REAL` — set when a start ends `started_ok` with no diagnosis, and when the payload's
  recheck clears a stored problem (the slow-entrypoint case). Never cleared.

Payload gains `origin` and `ran_ok` (bool). Also: a `failed_to_start` start stores its compose
`detail` as the project's problem (`code: failed_to_start`, message = the detail, trimmed to its
last meaningful lines) so the console can put "Docker said: …" in the prompt; it's cleared like
any other problem on the next good start. Builder: check `view.ts`'s `wrong()` still maps it.

## Console state (projects/view.ts)

Two new kinds, decided before the existing `wrong`/`stopped`/`waiting`:

| Condition | Kind | Badge |
|---|---|---|
| job running (up/restart/clone) | `starting` (unchanged) | Starting |
| `!ran_ok && empty && origin == new` | `waiting` | **Waiting for agent** |
| `!ran_ok && (empty && origin != new  ||  fault)` | `setup` | **Needs set-up** (amber) |
| everything else | as today | as today |

`fault` = what `wrong()` returns today (any cause). Stopped-with-no-fault and never-ran stays
`stopped`. `gone` (folder missing) still wins. After the first good run all failures are the
existing red `wrong` page. The kit's `StateBadge` gains the two states (design: StatusDot
`provisioning` / `degraded`).

## Agent naming

Copy uses the connected agent's display name (first connected in `runtime/agents/index.json`
order; from `GET /agents/status` + the catalog), else "your coding agent". The card header shows
a **Connected** badge or **Not connected**; **Switch agent** links to `/agents`. Below, "‹Agent›"
means that name.

## Screens

### 8a New project dialog
Title "New project". Name field, hint `Folder: ~/projects/<slug>`. "What happens next" block,
three rows (Eggie egg icon / agent mark): Eggie — "Makes an empty folder for it"; ‹Agent› —
"Builds it. You start it with one prompt we write for you." (highlighted row); ‹Agent› —
"Starts it with `eggie up` and gives you an address" (rephrased from the design's "Eggie starts
it"; user call). Buttons: Cancel, **Make the folder** → project page.

### 8b From GitHub dialog
Title "Bring in from GitHub". Keep the connect / device-code / reconnect flow. Ready state:
- A search field above the repo list. Typing filters by name (case-insensitive, over
  `full_name`); while a query is set, keep fetching `has_more` pages until all are loaded, then
  filter in the browser. `/user/repos` has no name filter.
- A click **selects** a repo (no clone yet). Under the list: the "What happens next" block
  (Eggie — "Copies the code to this computer"; ‹Agent› — "Sets it up to run in Eggie. Most
  projects need this once, so it's not a sign anything's broken."; ‹Agent› — "Starts it with
  `eggie up` and gives you an address"). Footer: Cancel, **Bring it in** (disabled until a repo is
  selected) starts the clone. Name-taken renaming and "Already a project → Open" keep working.

### From a folder (not in the design)
Same "What happens next" block as 8b with "Copies the folder into Eggie". After the upload
finishes, if the folder had a `docker-compose.yml`, the console calls `up` (same as clone), then
opens the project page. No compose → page shows Needs set-up. The upload has no API job, so
while it runs another tab may briefly show the new project as Needs set-up (my call: accepted).

### 8c Projects list
Subtitle "N on the go · M waiting on ‹Agent›" (M = waiting + setup; omit the second part at 0).
Rows: `waiting` → note "Empty folder · ‹Agent› builds it from one prompt", primary **Next step**;
`setup` → note "From GitHub · ‹Agent› makes it run here" ("From a folder · …" for folder
origin; "‹Agent› makes it run here" for new), secondary **Set it up**. Both go to the project page.
Notes carry the small agent mark.

### 8d / 8e Project page hand-off card
Replaces the current `waiting` body and is the `setup` body. Lead line:
- waiting: "Eggie made an empty folder at `~/projects/<id>`. Building what goes in it is
  ‹Agent›'s job."
- setup (github): "Copied from `<owner/repo>`. It doesn't start yet. That's normal: code from
  GitHub is set up for someone else's computer, and ‹Agent› can make it fit Eggie in one go."
  Folder/new wording: builder's call in the same tone. (The repo name: builder reads it from the
  clone's origin remote or stores it with `origin`; builder's call.)

Card, header "The next step happens in ‹Agent›" + Connected badge + Switch agent:
1. **Copy this prompt** — code block with the prompt; for setup a second dimmed line "Docker
   said: …" when a problem message exists; Copy button ("Copy the prompt" → "Copied").
2. **Paste it into ‹Agent› and press Enter** — waiting: "It asks what you want to build, then gets
   to work. Usually a few minutes." setup: "It reads the project, makes it run here and tells you
   which keys to add in Secrets."
3. **‹Agent› starts it when it's done** — "It runs `eggie up`; this page updates by itself." Setup
   adds a ghost **Try now** button (`up`, as Start does).

Prompts (in `projects/prompts.ts`, agent-neutral wording; path is `~/projects/<id>`):
- new: "I'm starting a new project in ~/projects/<id>. The folder is empty. Ask me a few short
  questions about what it should do, then build it there. Add a docker-compose.yml at the top of
  the folder that starts everything, with the web service listening on 0.0.0.0, and start it with
  `eggie up`. When you're done, tell me in plain words what you made and how to use it."
- setup: "I copied ~/projects/<id> from GitHub into Eggie and it doesn't start yet. Work out how
  this project is meant to run, then make it start with `eggie up`: add or fix docker-compose.yml
  at the top of the folder, with the web service listening on 0.0.0.0. Don't change what the app
  does. If it needs API keys or passwords, don't invent them. List each one so I can add it in
  Eggie → Secrets. Finish by telling me in plain words what you changed." ("from GitHub" → "from
  a folder" / dropped for new.) Docker's message is appended.

### 8f Analyze page
`/p/:id/analyze` inside `ProjectLayout`; the menu item becomes a NavLink; `AnalyzeModal` goes.
Lead: "Get a plain-language read on this project. Eggie doesn't look at your code. ‹Agent› reads
it and answers there. Nothing gets changed." "What do you want to know?" + a segmented control
(new kit component): Full once-over / Why won't it start? / Safe to share?. Default: "Why won't
it start?" when the project is `wrong` or `setup`, else Full once-over (builder's call). Card as
above with header "This happens in ‹Agent›"; step 2 hint "Takes a minute or two. Read the report
in ‹Agent›, not here."; step 3 "What you'll get back" chips per focus. Prompt texts (from the design, `<p>` = `~/projects/<id>`):
- full: "Look through <p> and give me a short, plain-language report. Don't change any files. Tell
  me: 1) what it does, in two sentences; 2) how it runs — what starts and what it needs; 3) what's
  broken or half-finished; 4) which keys or passwords it expects; 5) the three things you'd fix
  first, and why." Chips: What it does · How it runs · What's broken · Keys it needs · Top 3 fixes.
- start: "<p> won't start in Eggie. Find out why: check docker-compose.yml, the Dockerfile and the
  error below. Don't change any files yet. Explain the cause in plain words, tell me exactly what
  you'd change to fix it, then wait for my go-ahead." + the problem message. With no problem
  message, drop "and the error below" and the appended line. Chips: The cause, in plain words · The
  fix it suggests · It waits for your OK.
- share: "I'm about to put <p> on a public address. Check it first, without changing any files: are
  there keys, passwords or personal data in the code or in anything it serves? What breaks for
  someone who isn't me? Give me a short list: what's fine, and what to fix before sharing." Chips:
  Leaked keys or data · What breaks for others · What to fix first.
Copy-button hint: "It tells ‹Agent› not to change any files."

## After #92

When rebasing onto push to kitchen: Try now / Start follow `start-when-ready` when secrets are
requested; the migration renumbers after `_v7_push`; a project a kitchen receives
(`services/transfers.py`) is stored with `ran_ok_at` set and the sender's `origin` (it ran on the
sender, so a failure there is a real fault — my call).

## Testing

Worth testing: `projectView` across origin × ran_ok × empty × fault; the API setting
`ran_ok_at` (good start, diagnosed start, recheck clear) and `origin` per create path; the
`failed_to_start` problem message; the prompt builders (origin wording, Docker line, missing
problem); the repo search fetching remaining pages. Mock scenarios for waiting, setup (github,
folder), and analyze. Not worth testing: static copy, dialog layout.

## Left to the builder

Component structure, CSS, the exact trim of Docker's message, folder/new set-up wording,
mock scenarios, docs touch-ups (`runtime/web/CLAUDE.md`, `eggie_api/CLAUDE.md` feature notes).
