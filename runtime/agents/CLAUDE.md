# runtime/agents/ — one folder per coding agent

`index.json` orders the agents; `<id>/agent.json` describes one. Adding an agent is a folder plus
one line in `index.json` — no code changes. Two readers, each validating only what it uses:

- the console (`runtime/web/apps/console/src/agents/catalog.ts`) reads `name`, `note`, `icon`,
  `platforms` (the guide) and whether `setup` exists; served at `/agent-guides/`. `note` is shown
  as written after `name` in the agent list and menu; sentences use `name` alone. Only for an agent
  that lives inside another app, as Codex does in ChatGPT; the others leave it out;
- `runtime/install/lib/agents.py` reads the install-side fields below, on the VM.

| Field | Meaning |
|---|---|
| `home` | Directory under each account's home, e.g. `.codex`, or a list of them (Cursor: `.cursor` and its remote server's `.cursor-server`). Detection reads every one; `detect.ignore` applies to each. |
| `instructions` | Files that get `instructions/eggie.md`: `/abs/path` written whole once, `~/path` as the `<!-- eggie:begin/end -->` block per account. |
| `skills` | The `npx skills add -a` agent name. |
| `detect.ignore` | Top-level names under `home` that Eggie's own install creates (instructions, skills). Anything else there means the agent connected. A setup's own files count too. |
| `setup.run` | Shell command run once per account when the user opens the agent's guide. |

## The guide (`platforms.<mac|windows|cloud>`)

The console picks one by the VM (`/api/connect`'s `vm`): `wsl` → windows, `lima` → mac, a site →
cloud, otherwise the browser's OS. `cloud` is for any computer reaching a server over SSH with the
key the user downloaded from the web app.


- `tagline`, and `steps`: each `{title, body, alt}` plus an optional `media` list of files in this
  folder, e.g. `["mac/3a.png", "mac/3b.png", "mac/3.mp4"]`. The kind comes from the extension
  (png jpg jpeg webp gif svg avif / mp4 webm; a video plays muted on a loop). More than one makes a
  slider; none gives a placeholder showing `alt`, which is also each image's alt text.
- `auto` (optional): `{"steps": [...]}`, a shorter list shown instead of `steps` when the desktop
  window can add the connection itself (`connect=` in its link; Claude Code on Mac, Codex on
  Windows). Exactly one of its steps has `"action": "connect"`: the picture frame then shows a card
  with that step's title and body and the "Add Eggie to <agent>" button, so the step needs no
  `media` (any it has is not shown). That step alone may set `button` (the label) and `done` (the
  heading once it has run) when "Add Eggie to …" / "Eggie is in …'s connections" would be wrong.
  No other step may have an `action`. The list shows no card unless `auto` has its own `card`. `steps` stays complete: a plain browser, the cloud and "Set it up by
  hand instead" all use it.
- `warning` (optional): HTML shown above the steps, for a catch the user must handle before step 1
  (Codex on Windows only runs in the default WSL distro). Same HTML rules as `body`.
- `body` is HTML limited to `p ul ol li strong em b i code kbd br`, with no attributes. Anything
  else (a link, a class, an unclosed tag) drops the agent, and `content.test.ts` fails on it.
- `card` (optional): fields to copy, in the order and shape the agent's own form asks for — SSH
  details on Mac, a fixed path on Windows. Each is `{label, value}`, where `value` is plain text
  or a template over `{user}`, `{host}`, `{port}`, `{key_file}` — Claude Code wants
  `{"label": "SSH Host", "value": "{user}@{host}"}`. Any other placeholder drops the agent. A
  plain-text value is guidance and gets no copy button; an optional `hint` adds an info tooltip.

## Things that will bite you

- `setup.run` runs as root and as every login user. A manifest is as trusted as `install.sh`:
  review a new one like a script change.
- `detect.ignore` must list everything the install and `setup.run` put under `home`, or the agent reads as
  connected right after install. Check on a fresh VM with `ls -A ~/<home>`.
- This folder is copied into the web image (`--build-context agents=…`) and read from
  `/opt/eggie/runtime/agents` on the VM by both the runner and the API.
- An agent running outside the VM never sees its `~/` instructions. Codex on Windows reads
  `AGENTS.md` from the folder it opened, the reason for `/opt/eggie/projects/AGENTS.md`: there
  the stop-if-not-in-the-VM check still reaches it.
