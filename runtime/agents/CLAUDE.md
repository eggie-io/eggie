# runtime/agents/ — one folder per coding agent

`index.json` orders the agents; `<id>/agent.json` describes one. Adding an agent is a folder plus
one line in `index.json` — no code changes. Two readers, each validating only what it uses:

- the console (`runtime/web/apps/console/src/agents/catalog.ts`) reads `name`, `icon`,
  `platforms` (the guide) and whether `setup` exists; served at `/agent-guides/`;
- `runtime/install/lib/agents.py` reads the install-side fields below, on the VM.

| Field | Meaning |
|---|---|
| `home` | Directory under each account's home, e.g. `.codex`. Detection reads it. |
| `instructions` | Files that get `instructions/omelet.md`: `/abs/path` written whole once, `~/path` as the `<!-- omelet:begin/end -->` block per account. |
| `skills` | The `npx skills add -a` agent name. |
| `detect.ignore` | Top-level names under `home` that Omelet's own install creates (instructions, skills). Anything else there means the agent connected. |
| `setup.run` | Shell command run once per account when the user opens the agent's guide. |

## Things that will bite you

- `setup.run` runs as root and as every login user. A manifest is as trusted as `install.sh`:
  review a new one like a script change.
- `detect.ignore` must list everything the install puts under `home`, or the agent reads as
  connected right after install. Check on a fresh VM with `ls -A ~/<home>`.
- This folder is copied into the web image (`--build-context agents=…`) and read from
  `/opt/omelet/runtime/agents` on the VM by both the runner and the API.
