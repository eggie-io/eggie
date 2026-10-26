# You are working inside an Eggie VM
This is an isolated Linux VM. Projects live in ~/projects, one folder each,
and run in Docker behind Eggie's router.

**Before anything else, check where your commands run:** `test -d /opt/eggie/projects`.
If that folder is missing, or the command cannot run at all (a Windows shell), your commands
are not running inside the Eggie VM. Stop at once: create, install and run nothing, and
tell the user that their coding agent is not connected to Eggie and they should follow
its setup guide in Eggie again.

**Then read `~/.eggie/owner.md`** — how this user wants to work.

**No such file? Create it before doing anything they asked.** Ask once, in one message:
"Before we start, so I talk to you the right way: 1. Just show me results 2. Explain what
you're doing as we go 3. I'm a developer — talk tech and let me make technical choices".
Then write the file (`mkdir -p ~/.eggie`), the answer as its first line:

```
Level: just-results
```

1 → `just-results`, 2 → `explain`, 3 → `developer`; skipped, or they just carry on →
`just-results`, and never ask again. Tell them they can change it any time by telling you,
then continue with their request.

The `Level:` line says how to work with them:
- `just-results` (also when the line is missing or unknown): they are not technical. Never ask
  them technical questions (stack, ports, databases, frameworks) — decide yourself, and report
  what you did in plain words.
- `explain`: decide yourself as above, and say in one plain sentence what you chose and why;
  give a technical name a short plain gloss.
- `developer`: technical words are fine. Ask when a technical choice is real; their choice wins.

Notes go under the `Level:` line (name, language, how they like answers); follow them. When
they ask you to be more or less technical, or tell you how they like to work, update the file.
At every level the rules below hold, and always give them the project's URL.

- Start with `eggie status` to see what exists and what is running.
- Run projects only with `eggie up` — never `docker compose up` directly,
  or the project gets no URL.
- Never start a server yourself: no `npm run dev`, `vite`, `python -m http.server`,
  `uvicorn` and the like, not even for a quick check. Eggie cannot see, stop or remove
  such a process. Put it in the project's compose file and run `eggie up`.
- Never edit `.eggie/overlay.yml`; it is generated.
- Settings and secrets: use the project's `.env` as on any laptop — create it from the project's
  template, edit it for settings, and let framework commands write their own keys (Laravel's
  `php artisan key:generate`). A key or password from an outside service (Stripe, OpenAI, mail)
  is a secret: tell the user where to get it, run `eggie secret request NAME "where to get it"`,
  and ask them to fill it on the project's **Secrets** page in Eggie. Leave the name empty or out
  of `.env`; Eggie hands the value to every service as an environment variable, and it wins over
  `.env`. Never write a secret value into a file, the compose file or a commit. If the user insists
  on putting it in `.env`, do it, but tell them once that it then lives in the project folder. A
  service that sets the name to a non-empty literal in its own `environment:` beats Eggie: change it to
  `${NAME}`. A secret change needs a restart. `.env` loaders in override mode, config cached into an
  image and build-time variables don't see Eggie's values. `eggie secret list` shows what is set and
  requested. Run one-off commands with `docker exec <container> …` (find it with `docker ps`); a
  `docker compose run` you start gets none of Eggie's values.
- Something broken? `eggie logs`.
- An idea for an app, or a change to a project: start with the `eggie-brainstorm` skill.
- Something to set up, import or run — a repo URL, an archive, a folder: the `eggie-setup`
  skill (no skills? run `eggie --help`).
- Files on the user's own computer are out of reach from here. For a folder they have, ask them
  to use **Import** in Eggie. You cannot open their browser: give them links to click.
- GitHub — repositories, pull requests, issues: use `gh` and plain `git` over https.
  If `gh auth status` fails or GitHub says unauthorized, do not run `gh auth login`
  and do not ask for a token: tell the user to click **Connect GitHub** in Eggie,
  then try again.
