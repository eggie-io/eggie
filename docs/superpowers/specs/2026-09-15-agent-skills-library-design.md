# Agent skills library: brainstorm, stack, rules, plan — design

Date: 2026-09-15. Status: approved in brainstorming, pending spec review. Issue #5.

## Problem

The VM ships one skill, `eggie-setup`. It gets a project into `~/projects` and running, but for a
new app its whole guidance on what to build and with what is one line: "pick the simplest
mainstream stack for the job yourself". That produces three failures with a non-technical owner:

- The agent starts coding from a one-sentence idea and guesses at everything the owner did not
  say — who logs in, who edits content, what the first useful version is.
- "Simplest" is judged before the needs are known, so an owner who needs an editable site gets
  a hand-written HTML folder, and one who needs a shop gets a cart written from scratch.
- Nothing is written down. The next session (or the next agent — Codex and Claude Code share the
  VM) re-derives the stack, the conventions and the plan, and drifts.

## Goal

A coding agent working in the VM sets a project up the way a careful contractor would for a
client who cannot read code: find out what is wanted in plain words, choose the stack from the
needs, write down the rules of the house, plan before building anything non-trivial, and leave
documents the next session can pick up.

Non-goals: no dependency on the superpowers plugin inside the VM (its flow is copied, lightly);
no host change; no change to `engine/install.sh`, which installs every folder under
`engine/skills/`; no writing into `engine/` from a project.

## Decisions

- Five skills, all under `engine/skills/`: `eggie-setup` (rewritten, the orchestrator),
  `eggie-brainstorm`, `eggie-stack`, `eggie-rules`, `eggie-plan` (new).
- Every project carries its own documents: `docs/brief.md`, `docs/stack.md`,
  `docs/specs/<date>-<slug>.md`, `docs/plans/<date>-<slug>.md`, `AGENTS.md`, and a `CLAUDE.md`
  holding only `@AGENTS.md`. These are the owner's files in the owner's project; writing them
  is the point, unlike the engine's own files.
- Stack selection is language-neutral: a ladder of adopt → assemble → build, with the ecosystem
  chosen by the hardest need, hosting later, documentation depth, then consistency — in that order.
- Work is sized before it starts (small / feature / new project) by observable tests, and a task
  that grows is re-sized up, never finished at the smaller size.
- The owner is never asked a technical question. Every question is about needs, people, content,
  money, or what "done" looks like.
- Skills cross-reference each other by name only (`eggie-stack`), never by path.

## 1. Flows

### New app from a description

```
eggie new <name>
  → eggie-brainstorm   writes docs/brief.md (confirmed with the owner first)
  → eggie-stack        writes docs/stack.md (decision)
  → eggie-rules        writes AGENTS.md, CLAUDE.md, git init
  → eggie-plan         writes docs/plans/<date>-first-slice.md, then executes it
       step 1 is always: scaffold + docker-compose.yml + .eggie/project.yml, eggie up, URL answers
  → tell the owner the URL in one plain sentence
```

### Imported project (repository URL, archive, folder)

```
eggie clone <url>  (or unpack / move into ~/projects)
  → has docker-compose.yml?  yes: eggie up, prove the URL
                             no:  eggie-stack "existing project" path → compose → eggie up
  → eggie-rules "existing project" path: explore, reconstruct docs/brief.md (confirm),
    docs/stack.md (detected), AGENTS.md (extend, never overwrite)
```

The docs come after the URL because the URL is the deliverable and analysis takes minutes.

### Change to an existing project

```
size it (see §6)
  small   → confirm in one message, do it, verify, commit
  feature → eggie-brainstorm writes docs/specs/<date>-<slug>.md
          → eggie-plan writes docs/plans/<date>-<slug>.md and executes it
```

A project with no `AGENTS.md` gets `eggie-rules` before any code is written in it.

## 2. `eggie-brainstorm`

Purpose: turn an idea into a written brief (project) or spec (feature) using questions a
non-technical owner can answer. The questions are exactly the inputs `eggie-stack` needs plus
delivery scope; that coupling is the reason the two are separate skills and not one.

**Interview rules**

- One question per message. Multiple choice with plain examples where possible.
- Skip anything the description already answers. Restate what was understood before asking on.
- Scale to the request: a description that already answers everything gets a one-message
  confirmation, not the list. The list is the ceiling, not the script.
- No technology words in questions: not "database", "CMS", "login system", "API". Ask about
  people, content, money, memory, and outside things.
- End by reflecting the brief back in plain words and asking whether it is right. Write the file
  only after a yes.

**Question list (the ceiling)**

| Ask about | Why (what it decides) |
|---|---|
| What it is and who it is for, in a sentence or two | the archetype |
| Who uses it: only you, your team, customers or the public | accounts, roles, public exposure |
| The three main things a person should be able to do | screens, first slice |
| Will someone who is not a developer change text, pictures or products regularly | admin / CMS |
| Does it need to remember things between visits: accounts, orders, bookings, notes | data, database |
| Does it need to be "smart": chat, answer questions about documents, summarize, generate | AI; whether data processing pulls to Python |
| Does it connect to anything else: payments, email, calendar, a spreadsheet | integrations, payments |
| Will it stay on this computer, or go online later, and is there hosting already | hosting tie-breaker |
| What is the smallest version that would already be useful | first slice |
| Look and feel: a site whose style you like; the language(s) the app speaks | design, i18n |

**Output: `docs/brief.md`** (new project) with fixed sections: What it is · Who uses it · What
they can do · Content and who edits it · What it remembers · Outside services · Smart features ·
Where it will live · First slice · Later · Look and feel. Sections that do not apply say "none".

**Output: `docs/specs/<date>-<slug>.md`** (feature) with: What changes for the owner · Who it
affects · What it needs from outside · Not in this change · How we will know it works (a check
the owner could do in the browser). Read `docs/brief.md` first so questions build on it; update
the brief if the feature changes a section of it.

## 3. `eggie-stack`

Purpose: choose the stack from the brief, or detect it in an existing project, and record it.

### Client profile

The maintainer is a coding agent plus a non-technical owner. That shifts the usual trade-offs:
conventions and documentation depth over elegance (the agent works from what it has seen most),
batteries-included over assembled (every future change means fewer decisions), buy over build
for commodity parts (auth, admin, CMS, payments, email are products or libraries, never custom).

### Principles, in priority order

1. Cover every need in the brief natively. A CMS is the simple choice when editors exist and
   bloat when they do not. "Simplest" is measured after the needs, never before.
2. Boring and mainstream: deepest documentation, biggest community, LTS releases. Nothing released
   in the last year, nothing with a small community.
3. Buy the commodity parts. A hand-rolled login or admin panel is a decision failure.
4. One language front and back unless a need demands a second; never three.
5. SQLite until several people write at once or a chosen component needs Postgres or MySQL; then
   one database service with a named volume.
6. Reversible where cheap (ORM in front of the database), recorded where not (why this framework).
7. Fits Eggie: everything in compose, a dev server that reloads on a mounted source, listens on
   `0.0.0.0`, no published host ports, starts in seconds, web services declared in
   `.eggie/project.yml`.

### The ladder: adopt → assemble → build

1. **Adopt** when a mature open-source product covers the whole need. Run it in compose,
   customize through its own themes, plugins and configuration. The owner gets a finished admin
   and years of documentation instead of custom code.
2. **Assemble** when a product covers a part and the rest needs a custom face: a framework front
   with a headless product behind it.
3. **Build** when nothing fits, on a batteries-included framework in the ecosystem the hardest
   need points to.

### Choosing the ecosystem when building (no default language)

| Ecosystem | Home ground | Typical picks |
|---|---|---|
| PHP | content sites, shops, anything near WordPress or Magento, business apps; cheapest hosting later | WordPress, Laravel + Filament |
| Node / TypeScript | rich interactive UI, real-time, one language front and back, largest frontend ecosystem; AI features that are only model API calls (AI SDK) | Next.js, Astro, Hono, Prisma |
| Python | data processing, embeddings pipelines, ML libraries, scientific and automation work; admin-heavy business apps | Django (+ admin), FastAPI, Streamlit |
| Ruby | business apps; mature, smaller pool — an accepted alternative, not a default | Rails |
| Go, Java, .NET | only when integrating with an existing system that already uses them | — |

An AI chat feature alone does not pull a project to Python; data processing does.

**Tie-breakers, in order:** the ecosystem where the hardest need is covered natively → where the
project can be hosted or handed over most easily later (from the brief) → documentation depth →
consistency with the owner's other projects in `~/projects`.

### Archetypes: shape → candidates (product first) → when each wins

| Shape | Candidates and the condition that makes each win |
|---|---|
| Landing page, portfolio, brochure site | Astro static when the owner edits rarely; WordPress when anyone non-technical edits |
| Content site, blog, publication | WordPress (widest); Ghost (publishing, newsletters); Astro/Next.js + Payload/Strapi/Directus when a custom front matters |
| Shop | WooCommerce (small, content-heavy shop); Magento Open Source (large catalog, B2B pricing, multi-store/currency); PrestaShop (EU small business); Medusa + Next.js storefront (the shopping experience is the product); Saleor (Python house, GraphQL) |
| Business app: bookings, inventory, CRM-lite, forms with staff behind them | Odoo when it is really ERP/CRM/invoicing; Laravel + Filament; Django + admin; Rails |
| Bookings / appointments only | Cal.com (adopt); else the business-app row |
| Rich interactive app: dashboards with heavy client UI, editors, drag-and-drop | Next.js + Prisma + Auth.js; SvelteKit as an accepted alternative |
| AI app: chat, questions over documents, generation | Next.js + AI SDK when the AI is model calls in a JS app; FastAPI/Django backend + Next.js front when documents are processed, embedded or analysed; Streamlit alone for an internal tool |
| Data / internal dashboard | Metabase (adopt, on an existing database); Streamlit (build quickly); Django when it needs accounts and forms |
| Automation, bots, scheduled jobs | n8n (adopt); Python + scheduler + a small status page |
| Backend only (for a phone app or other systems) | FastAPI or Hono, with OpenAPI docs |
| Real-time collaboration, chat between users | Next.js + Socket.IO + Postgres + Redis |
| Community, forum | Discourse (adopt) |
| Docs, wiki, knowledge base | Docusaurus (public docs); BookStack or Outline (team wiki) |

**Overrides that cut across rows:** non-technical editors → an admin exists (the product's own,
Django admin, Filament, Payload); public accounts → an auth library, never hand-rolled; a CMS,
shop or concurrent writers → Postgres or MySQL as the product prefers; payments → Stripe's official
library, card data never stored; data processing → Python for that part.

**Anti-patterns named in the skill:** microservices; more than two languages; hand-rolled auth or
admin; frameworks chosen for novelty; anything justified by "we might need it later"; asking the
owner to choose between technologies.

### Existing project path

Detect, do not choose: `package.json` (+ `next`, `astro`, `svelte`…), `manage.py`, `pyproject.toml`
/ `requirements.txt` (+ `fastapi`, `django`, `streamlit`), `composer.json` (+ `laravel`,
`wp-config.php`), `Gemfile`, `go.mod`, `pom.xml`, `*.csproj`, existing `Dockerfile`s. Write the
compose file for what is there, from the matching recipe when one exists.

### Output: `docs/stack.md`

Sections: Needs (from the brief, one line each) · Ladder step (adopt / assemble / build) and the
product or framework · Why · Rejected (alternatives and the one reason each lost) · Would change
this if (the condition that reopens the decision) · Services (name, image or build, role, data
volume). For an existing project the first line says "detected, not chosen".

### References (progressive disclosure)

`engine/skills/eggie-stack/references/<recipe>.md`, one per likely default, read only when that
recipe is chosen: services, a compose skeleton that follows the Eggie contract, the dev command
with its reload flags, `.eggie/project.yml`, first-run steps (migrations, admin user), gotchas.
Planned recipes: `astro.md`, `nextjs.md`, `wordpress.md`, `woocommerce.md`, `laravel.md`,
`django.md`, `fastapi-nextjs.md`, `streamlit.md`, `payload.md`, `medusa.md`, `magento.md`. For any
other product the skill says: use the product's official compose and adapt it to the contract.
Every recipe also carries the "existing project" variant where it differs (mount, install step).

## 4. `eggie-rules`

Purpose: write the rules of the house into the project so every session — Claude Code or Codex —
works the same way. `AGENTS.md` is the source; `CLAUDE.md` contains only `@AGENTS.md`.

**Two paths, chosen by whether the project already has code.**

New project: fill the template from `docs/brief.md` and `docs/stack.md`, `git init`, first commit.

Existing project: explore first — manifests, entrypoints, how it is run and tested, folder layout,
existing conventions, existing `AGENTS.md`/`CLAUDE.md`/`CONTRIBUTING.md`. Reconstruct
`docs/brief.md` from what the code does and confirm the summary with the owner in plain words.
Write `docs/stack.md` as detected unless `eggie-stack` already did. Then `AGENTS.md`: if one exists, keep it and append a marked
`<!-- eggie:begin -->` … `<!-- eggie:end -->` section (the same markers `install-agents.sh`
uses) holding only the Eggie-specific parts; if none exists, write the full template.

**Template sections (`assets/AGENTS.md`)**, each with one line of guidance on what to put there;
sections that do not apply are removed, not left empty:

- What this project is — two sentences, pointing at `docs/brief.md` and `docs/stack.md`.
- Running it — `eggie up`, `eggie logs`, `eggie status`; language tools only through
  `docker compose run --rm <service> …`; never `docker compose up` directly; never install
  toolchains on the VM.
- Layout — where things live, five lines at most.
- Conventions of this stack — how migrations run, where routes/pages/components go, the formatter
  if the framework ships one.
- Testing — a test must be able to fail for a reason that matters; no tests that only prove a
  function exists, a framework works, or a type is accepted; no tests for coverage; test through
  the public surface; when unsure, do not write it and say so.
- Comments — only for edge cases, workarounds and non-obvious logic; never restate the code.
- Talking to the owner — plain words, no technical questions, always the URL, verify with curl
  before saying it works, report failures honestly.
- Git — commit after each working step with a message saying what changed for the owner.
- Working on changes — size it (small / feature); a feature gets a spec and a plan in `docs/`.
- Keeping this file current — add a non-obvious learning here when found; a subfolder with its
  own rules gets its own `AGENTS.md`; a changed stack decision updates `docs/stack.md`.

## 5. `eggie-plan`

Purpose: plan before building anything non-trivial, and execute the plan one verifiable step at
a time so a new session can resume.

**Input:** `docs/brief.md` (new project, first slice) or `docs/specs/<date>-<slug>.md` (feature).

**Output: `docs/plans/<date>-<slug>.md`**, a numbered list of steps. Each step is small enough
to finish and check in one go, and holds: files it touches · the change · how to check it (a
curl against the project URL, a page to open, a command through compose, a test to run) · a
checkbox. The first step of a new project is always "scaffold + compose + project.yml, `eggie
up`, URL answers". Steps that add a screen end with the owner being able to open it.

**Executing:** take the first unticked step; do it; run its check; tick it in the file; commit.
If the check fails, fix within the step; if the step turns out to hide more work, split it in the
file rather than pushing on. When all steps are ticked, report to the owner in plain words with
the URL and what they can try. A new session with an unticked plan continues from the first
unticked step.

## 6. Sizing (in `eggie-setup` and `AGENTS.md`)

| Size | Test (observable) | What happens |
|---|---|---|
| Small | the finished result fits in one sentence and touches a few files | confirm in one message, do it, verify, commit |
| Feature | adds a capability (accounts, payments, admin, a new screen flow, an integration), or touches many files, or the result cannot be stated in one sentence | brainstorm → spec → plan → execute |
| New project | no code yet | brief → stack → rules → plan → execute |

When in doubt, one size up. A task that grows mid-way is re-sized up, never finished small.

## 7. `eggie-setup` (rewritten)

Keeps: the "get it into `~/projects`" table, the rename/compose-file-name handling, the clone
failure wording, the Eggie compose contract (`0.0.0.0`, no host ports, `.eggie/project.yml`
`web:` key, mounted source with reload, data in a volume), and "prove the URL answers with curl".
Adds: the three flows of §1 as the body, the sizing table of §6, and hand-offs by skill name.
Drops: "pick the simplest mainstream stack" and the inline compose-writing advice that recipes
now carry.

`engine/instructions/eggie.md` gains one line: an idea for an app or a change to a project
starts with `eggie-brainstorm`; anything to set up or run starts with `eggie-setup`.

## 8. Skill format

- Frontmatter `name` equals the folder name; `description` starts with "Use when…", third person,
  triggers only, no workflow summary (an agent otherwise follows the description instead of the
  body), under 500 characters, and "pushy" enough that a plain-words request triggers it.
- Body under 500 lines; imperative; explains why. Heavy reference goes to `references/`,
  templates to `assets/`.
- Cross-references by skill name in bold: **Use `eggie-stack`** — never by path, never with `@`.

## 9. Testing

**Repo tests, `tests/engine/test_skills.py`** (real failures each catches):

- Every `engine/skills/*/SKILL.md` has frontmatter with `name` equal to its folder and a
  non-empty `description` — `npx skills add` registers by name, and a mismatch installs a skill
  nobody can call.
- Every `eggie-<x>` name mentioned in any SKILL.md or in `engine/instructions/eggie.md` is an
  existing folder — a rename silently breaks a hand-off.
- Every relative file referenced from a SKILL.md (`references/…`, `assets/…`) exists.
- The tree is resolved from `__file__` and the test asserts it scanned at least five skills, per
  the repo's vacuous-pass rule.

**Skill behaviour (writing-skills RED → GREEN), run through subagents in a scratch directory
before finalizing**, baseline without the skill and then with it:

1. "A site for my bakery; my sister updates the menu and the photos every week" → a product with
   an admin (WordPress or a CMS), not hand-written HTML; `docs/stack.md` names the editor need.
2. "I upload contracts and ask questions about them" → a document-processing backend (Python) and
   a web front; the decision record says why not Node.
3. "A page with my opening hours and phone number" → no interview marathon: one confirmation, a
   static site.
4. An existing Django repository → `eggie-rules` writes an `AGENTS.md` with the real run and
   migration commands, and does not overwrite an existing `CONTRIBUTING.md`-derived section.
5. "Add customer accounts to my shop" on a project with a brief → sized as a feature: a spec and a
   plan appear before code.

Baseline runs document what the agent does without the skill, verbatim, so each skill addresses
observed failures rather than imagined ones.

## 10. Out of scope

- Updating skills on an existing VM (needs engine self-update, `docs/future/engine-self-update.md`).
- Recipes for every product in the archetype table; the skill defers to official compose files.
- Any change to the host, the agent API or `engine/install.sh`.
- Design/visual-quality guidance for the frontend (a later skill).
