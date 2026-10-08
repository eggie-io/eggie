# You are working inside an Eggie VM
This is an isolated Linux VM. Projects live in ~/projects, one folder each,
and run in Docker behind Eggie's router.

**Before anything else, check where your commands run:** `test -d /opt/eggie/projects`.
If that folder is missing, or the command cannot run at all (a Windows shell), your commands
are not running inside the Eggie VM. Stop at once: create, install and run nothing, and
tell the user that their coding agent is not connected to Eggie and they should follow
its setup guide in Eggie again.

The user is not technical. Never ask them technical questions (stack, ports,
databases, frameworks) — decide yourself. Report results in plain words and
always give them the project's URL.

- Start with `eggie status` to see what exists and what is running.
- Run projects only with `eggie up` — never `docker compose up` directly,
  or the project gets no URL.
- Never start a server yourself: no `npm run dev`, `vite`, `python -m http.server`,
  `uvicorn` and the like, not even for a quick check. Eggie cannot see, stop or remove
  such a process. Put it in the project's compose file and run `eggie up`.
- Never edit `.eggie/overlay.yml`; it is generated.
- Settings and secrets: `.env.example` at the project root lists every variable the project
  needs — a secret as `NAME=` (empty), a non-secret setting as `NAME=default`. Eggie hands all of
  them to every service as environment variables; the user fills secrets and can change defaults
  on the project's **Secrets** page. Never write a secret value into any file, the compose file
  or a commit, and never create `.env`; if a tool writes one, move its non-secret keys into
  `.env.example`, then delete it. A random internal key (app secret, JWT/cookie secret):
  generate it straight into Eggie with `openssl rand -hex 32 | eggie secret set NAME`. Laravel's
  APP_KEY: `echo "base64:$(openssl rand -base64 32)" | eggie secret set APP_KEY`. A key
  from an outside service (Stripe, OpenAI, mail): add `NAME=` and ask the user to fill it on the
  Secrets page, then restart. After changing a default in `.env.example`, restart too.
  `eggie secret list` shows what is set and what still needs a value. Run one-off commands inside
  the running container with `docker exec <container> …` (find it with `docker ps`); a
  `docker compose run` or `exec` you start re-reads the compose file without Eggie's variables.
- Something broken? `eggie logs`.
- An idea for an app, or a change to a project: start with the `eggie-brainstorm` skill.
- Something to set up, import or run — a repo URL, an archive, a folder: the `eggie-setup`
  skill (no skills? run `eggie --help`).
- GitHub — repositories, pull requests, issues: use `gh` and plain `git` over https.
  If `gh auth status` fails or GitHub says unauthorized, do not run `gh auth login`
  and do not ask for a token: tell the user to click **Connect GitHub** in Eggie,
  then try again.
