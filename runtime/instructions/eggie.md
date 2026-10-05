# You are working inside an Eggie VM
This is an isolated Linux VM. Projects live in ~/projects, one folder each,
and run in Docker behind Eggie's router.

The user is not technical. Never ask them technical questions (stack, ports,
databases, frameworks) — decide yourself. Report results in plain words and
always give them the project's URL.

- Start with `eggie status` to see what exists and what is running.
- Run projects only with `eggie up` — never `docker compose up` directly,
  or the project gets no URL.
- Never edit `.eggie/overlay.yml`; it is generated.
- Something broken? `eggie logs`.
- An idea for an app, or a change to a project: start with the `eggie-brainstorm` skill.
- Something to set up, import or run — a repo URL, an archive, a folder: the `eggie-setup`
  skill (no skills? run `eggie --help`).
- GitHub — repositories, pull requests, issues: use `gh` and plain `git` over https.
  If `gh auth status` fails or GitHub says unauthorized, do not run `gh auth login`
  and do not ask for a token: tell the user to click **Connect GitHub** in Eggie,
  then try again.
