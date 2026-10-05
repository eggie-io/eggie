# Connect a coding agent — console guide

Issue #21. Implements section 4 ("Help, from Home, for any agent") of the Eggie Desktop design in
the runtime console. Runtime only: no host change, no `API_VERSION` bump.

## What the user gets

A signed-in console shows a **Connect an agent** button in the top bar. It opens:

- `/agents` — "Which agent do you use?": a grid of agent cards, four across, each with icon, name
  and a one-line tagline.
- `/agents/:id` — the guide: numbered steps on the left (the active one expanded with its body),
  the active step's screenshot on the right, `Step n of N`, Back / Next (Next reads "Done" on the
  last step and returns to the project list). An agent dropdown in the guide's bar switches agent
  without going back, plus "See all agents".

For now: Claude Code and Codex. Users connect from the agent's **app** (Claude Code UI, Codex UI),
never from a terminal.

## Platform

The guide shown is the one for the platform the VM runs on: WSL → `windows`, Lima → `mac`. The
console reads that from `GET /api/connect`. When the API answers `vm: "other"` (an install from
before this change, a cloud VM) the console falls back to the browser: a `Mac` in
`navigator.userAgent` means `mac`, anything else `windows`.

## Content: JSON files, no per-agent code

Static files in the `eggie-web` image, `runtime/web/apps/console/agent-guides/`, served at
`/agent-guides/` (never under a console route: nginx serves that path with no SPA fallback) and
fetched at page load. Adding an agent is a data change plus a runtime release.

```
agent-guides/index.json            {"agents": ["claude-code", "codex"]}   picker order
agent-guides/<id>/agent.json
agent-guides/<id>/icon.svg
agent-guides/<id>/<platform>/<n>.png     screenshots, optional
```

```json
{
  "name": "Claude Code",
  "icon": "icon.svg",
  "platforms": {
    "mac": {
      "via_ssh": true,
      "tagline": "SSH connection",
      "steps": [
        {"title": "Open Claude Code", "body": "…", "screenshot": "mac/1.png", "alt": "Claude Code start screen"}
      ]
    },
    "windows": {"via_ssh": false, "tagline": "Inside WSL", "steps": ["…"]}
  }
}
```

- Paths (`icon`, `screenshot`) are relative to the agent's folder.
- An agent with no block for the current platform is left out of the picker; `/agents/<id>` for it
  redirects to `/agents`.
- `screenshot` is optional. Without it the frame shows a placeholder captioned with `alt`.
- `catalog.ts` validates every file. A malformed agent is dropped (and logged); a missing or
  malformed `index.json` shows an error state on `/agents`, never a blank screen.

## `via_ssh` and the connection card

- `via_ssh: true` — a "Your kitchen's key" card under the steps with four separate lines, each
  with its own copy button: **Host** `127.0.0.1`, **Port** `39022`, **User** `<guest login user>`,
  **Key file** `~/.lima/_config/user`. A one-line note says the port is the one the VM asks for.
  No command line, no password (Lima is key-only).
- `via_ssh: false` — no card. The steps themselves say how to pick the WSL machine in the app.

## Runtime: `GET /connect`

`install.sh` writes `/opt/eggie/connect.json` (bind-mounted into the API container):

```json
{"vm": "lima", "user": "ihor"}
```

- `vm`: `wsl` when `/proc/sys/fs/binfmt_misc/WSLInterop` exists, `lima` when `/mnt/lima-cidata`
  exists, otherwise `other`.
- `user`: the first account from `lib/login-users.sh`, or empty.

`GET /connect` (so also `/api/connect`, behind the same auth as every route) reads it and answers:

```json
{"vm": "lima", "ssh": {"host": "127.0.0.1", "port": 39022, "user": "ihor", "key_file": "~/.lima/_config/user"}}
```

`ssh` is `null` unless `vm` is `lima` and `user` is non-empty. A missing or unreadable file answers
`{"vm": "other", "ssh": null}`. The port and key file are constants in the API, mirroring the host's
`eggie.yaml` `ssh.localPort` and Lima's default key path.

## First content

Four guides, four steps each, text only (no screenshots yet), marked in the PR as a draft to edit:
Claude Code and Codex, each for mac (SSH connection in the app) and windows (the app's WSL option).

## Tests

- `catalog.ts`: platform filtering, a malformed agent dropped, a missing screenshot kept as a
  placeholder, paths resolved against the agent folder.
- Card rows from `/connect` + `via_ssh`: no card for `via_ssh: false`; for `via_ssh: true` with
  `ssh: null`, the rows show what is known and the user line says it is unknown.
- `GET /connect`: file present, missing, corrupt, `wsl` with a user still gives `ssh: null`.
- `install.sh`: `bash -n` still passes and it writes `connect.json`.
- Mock API gains `/api/connect` so `npm run dev` shows the flow.

Left untested: the React screens themselves (layout and step switching are glue over the tested
catalog and card mapping).
