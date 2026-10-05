# Agent Skills Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four new in-VM skills (`eggie-brainstorm`, `eggie-stack`, `eggie-rules`, `eggie-plan`) and rewrite `eggie-setup` as their orchestrator, with a repo test that keeps the set installable and cross-linked.

**Architecture:** Skills are Markdown folders under `engine/skills/`, installed per account by `npx skills add "$ENGINE_DIR/skills" -s '*'` (no installer change). Each skill is a `SKILL.md` with `name`/`description` frontmatter; heavy material goes to `references/` (stack recipes) and `assets/` (the AGENTS.md template). A pytest under `tests/engine/` checks structure and cross-references from `__file__`.

**Tech Stack:** Markdown, YAML frontmatter, pytest (stdlib only, no YAML parser — the host rule about parsing no YAML does not apply to tests, but a regex keeps the test dependency-free).

**Spec:** `docs/superpowers/specs/2026-09-15-agent-skills-library-design.md`

## Global Constraints

- Frontmatter `name` equals the folder name; `description` starts with `Use when`, third person, triggers only, no workflow summary, under 500 characters.
- Skills reference each other by name only (`eggie-stack`), never by path, never with `@`.
- No technology words in any question the agent is told to ask the owner.
- No default language anywhere in `eggie-stack`; every archetype row lists candidates with the condition that makes each win.
- Project documents: `docs/brief.md`, `docs/stack.md`, `docs/specs/<date>-<slug>.md`, `docs/plans/<date>-<slug>.md`, `AGENTS.md`, `CLAUDE.md` = `@AGENTS.md`.
- Eggie compose contract in every recipe: listen on `0.0.0.0`, no published host ports, `.eggie/project.yml` with only a `web:` key, source mounted with a reloading dev server, data in a named volume.
- `engine/install.sh` is not touched. Tests resolve the tree from `__file__` and assert they scanned something.
- Commit messages end with the session's attribution lines.

---

### Task 1: Structural test for the skills folder

**Files:**
- Create: `tests/engine/test_skills.py`

**Interfaces:**
- Produces: the invariants every later task must satisfy (name = folder, description present, referenced skills and files exist).

- [ ] **Step 1: Write the failing test**

```python
"""Every folder under engine/skills is installed by `npx skills add` and
registered by its frontmatter name; the skills hand off to each other by
name. A mismatch or a rename installs a skill nobody can call."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "engine" / "skills"
INSTRUCTIONS = ROOT / "engine" / "instructions" / "eggie.md"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SKILL_REF = re.compile(r"\beggie-[a-z]+(?:-[a-z]+)*\b")
_LOCAL_FILE = re.compile(r"`((?:references|assets)/[^`]+)`")


def _skills() -> dict[str, Path]:
    found = {p.name: p / "SKILL.md" for p in SKILLS.iterdir() if p.is_dir()}
    assert len(found) >= 5, f"scanned {SKILLS}, found {sorted(found)}"
    return found


def _frontmatter(skill_md: Path) -> dict[str, str]:
    m = _FRONTMATTER.match(skill_md.read_text())
    assert m, f"{skill_md} has no frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_frontmatter_name_matches_the_folder():
    for folder, skill_md in _skills().items():
        fm = _frontmatter(skill_md)
        assert fm.get("name") == folder, f"{skill_md}: name={fm.get('name')!r}"


def test_description_states_when_to_use_and_stays_short():
    for folder, skill_md in _skills().items():
        description = _frontmatter(skill_md).get("description", "")
        assert description.startswith("Use when"), f"{folder}: {description[:40]!r}"
        assert len(description) <= 500, f"{folder}: {len(description)} chars"


def test_every_skill_named_in_a_hand_off_exists():
    skills = _skills()
    sources = list(skills.values()) + [INSTRUCTIONS]
    for src in sources:
        for name in set(_SKILL_REF.findall(src.read_text())):
            assert name in skills, f"{src.relative_to(ROOT)} refers to {name}"


def test_every_bundled_file_a_skill_points_at_exists():
    for folder, skill_md in _skills().items():
        for rel in set(_LOCAL_FILE.findall(skill_md.read_text())):
            assert (skill_md.parent / rel).is_file(), f"{folder}: {rel}"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/engine/test_skills.py -q`
Expected: FAIL in `_skills()` — only one folder exists (`found ['eggie-setup']`).

- [ ] **Step 3: Commit the test alone** (it stays red until Task 6; the branch is not merged before then)

```bash
git add tests/engine/test_skills.py
git commit -m "test: skills folder is installable and cross-linked"
```

---

### Task 2: `eggie-brainstorm`

**Files:**
- Create: `engine/skills/eggie-brainstorm/SKILL.md`

**Interfaces:**
- Produces: `docs/brief.md` with sections *What it is · Who uses it · What they can do · Content and who edits it · What it remembers · Outside services · Smart features · Where it will live · First slice · Later · Look and feel*; `docs/specs/<date>-<slug>.md` with *What changes for the owner · Who it affects · What it needs from outside · Not in this change · How we will know it works*. `eggie-stack` and `eggie-plan` read these section names.

- [ ] **Step 1: Write SKILL.md** with: frontmatter (`Use when the owner describes an app, a site, a tool or a change to one in plain words and no written brief or spec exists yet…`); Overview (why a brief before code: guessing the unsaid, and the brief feeding `eggie-stack`); *Interview rules* (one question per message, multiple choice with plain examples, skip what is answered, scale to the request, no technology words — with a table of forbidden words and the plain replacement, reflect back and get a yes before writing); *The question list* as the spec's table with the "why" column; *New project → docs/brief.md* with the section list and a short filled example for a bakery site; *Feature → docs/specs/* with the section list, "read docs/brief.md first, update it if a section changes"; *Common mistakes* (asking two things at once, asking about databases, writing the brief before the yes, running the full list for a landing page).
- [ ] **Step 2: Check length and lint** — `wc -l` under 200; description under 500 chars.
- [ ] **Step 3: Commit** `feat(skills): eggie-brainstorm turns an idea into a brief or spec`

---

### Task 3: `eggie-stack` with recipes

**Files:**
- Create: `engine/skills/eggie-stack/SKILL.md`
- Create: `engine/skills/eggie-stack/references/{astro,nextjs,wordpress,woocommerce,laravel,django,fastapi-nextjs,streamlit,payload,medusa,magento}.md`

**Interfaces:**
- Consumes: `docs/brief.md` sections from Task 2.
- Produces: `docs/stack.md` with sections *Needs · Ladder step · Why · Rejected · Would change this if · Services*; the first line `Detected, not chosen.` on the existing-project path. `eggie-rules` reads *Services* and the recipe's commands.

- [ ] **Step 1: Write SKILL.md** with: frontmatter (`Use when a brief exists and no code does, when a project has no docker-compose.yml, or when the owner wants a database, admin, shop, payments, AI or any new capability added — before any framework, language or product is named…`); *The client* (agent + non-technical owner: conventions over elegance, batteries over toolkits, buy commodity parts); *Principles in priority order* (the spec's seven); *The ladder* adopt → assemble → build with the product list; *Choosing the ecosystem when building* table (PHP / Node / Python / Ruby / others) with "an AI chat feature alone does not pull to Python; data processing does"; *Tie-breakers in order*; *Archetypes* table exactly as the spec; *Overrides*; *Anti-patterns*; *Existing project: detect, do not choose* with the manifest table; *Record the decision* with the `docs/stack.md` section list and a filled example (bakery → WordPress, why not Astro); *Then* → read the matching `references/<recipe>.md`, or the product's official compose for anything without one, and hand back to `eggie-setup`.
- [ ] **Step 2: Write the eleven recipes**, each with the same headings: *When this recipe* · *Services* · *docker-compose.yml* (full skeleton following the contract) · *.eggie/project.yml* · *First run* (install, migrate, create admin, through `docker compose run --rm`) · *Existing project* (what differs) · *Gotchas* (the reload flag, `0.0.0.0` host, file permissions on mounted volumes, Node inside Docker needs `HOSTNAME=0.0.0.0` for Next, WordPress needs `WORDPRESS_CONFIG_EXTRA` for the sslip host, Magento's memory and first-install time).
- [ ] **Step 3: Verify** every recipe named in SKILL.md exists: `python3 -m pytest tests/engine/test_skills.py::test_every_bundled_file_a_skill_points_at_exists -q` (still fails on `_skills()` count until Task 6 — check the file list by hand with `ls`).
- [ ] **Step 4: Commit** `feat(skills): eggie-stack chooses the stack from needs, product first`

---

### Task 4: `eggie-rules` with the AGENTS.md template

**Files:**
- Create: `engine/skills/eggie-rules/SKILL.md`
- Create: `engine/skills/eggie-rules/assets/AGENTS.md`

**Interfaces:**
- Consumes: `docs/brief.md`, `docs/stack.md`.
- Produces: `AGENTS.md`, `CLAUDE.md` containing `@AGENTS.md`, a git repository with a first commit; on an existing `AGENTS.md`, a `<!-- eggie:begin -->` … `<!-- eggie:end -->` block.

- [ ] **Step 1: Write assets/AGENTS.md** — the ten sections of the spec (§4), each with an italic one-line hint of what to write there, and the testing and comment sections already worded (they are policy, not project-specific).
- [ ] **Step 2: Write SKILL.md** with: frontmatter (`Use when a project has no AGENTS.md or CLAUDE.md, when an imported repository is about to be worked on, or right after the stack of a new project is chosen — before the first line of code is written in it…`); *Why* (two agents share the VM; rules unwritten are rules re-decided); *Which path* by the predicate "the project already has code"; *New project* steps (fill template from brief and stack, remove sections that do not apply, `git init`, `.gitignore` from the recipe, first commit); *Existing project* steps (explore list, reconstruct brief and confirm with the owner, detected stack record unless present, extend an existing file inside the markers, never overwrite, commit if the repo is git); *Keep it small* (a page, not a manual); *Common mistakes* (leaving template hints in, restating framework docs, overwriting the owner's file).
- [ ] **Step 3: Commit** `feat(skills): eggie-rules writes the project's AGENTS.md`

---

### Task 5: `eggie-plan`

**Files:**
- Create: `engine/skills/eggie-plan/SKILL.md`

**Interfaces:**
- Consumes: `docs/brief.md` (first slice) or `docs/specs/<date>-<slug>.md`.
- Produces: `docs/plans/<date>-<slug>.md` — numbered steps, each `- [ ]` with *Files · Change · Check*; ticked in place as executed.

- [ ] **Step 1: Write SKILL.md** with: frontmatter (`Use when a brief or spec exists and code is about to be written for it, when a change adds a capability or touches many files, or when a docs/plans file has unticked steps to continue…`); *Why plan* (agents build better from a list of checked steps; the file survives the session); *Write the plan* (step shape, first step of a new project is scaffold + compose + `eggie up` + URL answers, every screen step ends with something the owner can open, sizes: a step is done and checked in one go); a short example plan (bakery first slice, four steps); *Execute* (first unticked step → do → check → tick → commit; a failing check is fixed inside the step; a step hiding more work is split in the file; finish with a plain-words report and the URL); *Resume* (a new session continues from the first unticked step); *Common mistakes* (steps without a check, ticking before the check, pushing through a step that grew).
- [ ] **Step 2: Commit** `feat(skills): eggie-plan plans and executes verifiable steps`

---

### Task 6: `eggie-setup` rewrite and the instructions line

**Files:**
- Modify: `engine/skills/eggie-setup/SKILL.md` (rewrite)
- Modify: `engine/instructions/eggie.md:14-15`

**Interfaces:**
- Consumes: the four skills by name.

- [ ] **Step 1: Rewrite SKILL.md** keeping the get-it-into-`~/projects` table, rename and compose-file-name handling, clone-failure wording, the compose contract, and the curl check; body becomes the three flows (new app, imported, change) plus the sizing table; hand-offs as **Use `eggie-brainstorm`** etc.; drop "simplest mainstream stack" and the inline compose advice recipes now carry.
- [ ] **Step 2: Edit eggie.md** — replace the last bullet with two: an idea for an app, or a change to a project → `eggie-brainstorm`; something to set up, import or run → `eggie-setup` (keep the "no skills? run `eggie --help`" tail).
- [ ] **Step 3: Run the structural test and the full suite**

Run: `python3 -m pytest tests/engine/test_skills.py -q && TMPDIR=$PWD/.tmp python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit** `feat(skills): eggie-setup orchestrates brainstorm, stack, rules and plan`

---

### Task 7: Behaviour check with subagents (RED → GREEN)

**Files:**
- Scratch only: `<scratchpad>/skill-runs/<scenario>/{baseline,with-skill}/`

- [ ] **Step 1: Baseline** — for scenarios 1–3 of the spec (§9) dispatch a subagent with the scenario as the owner's message, a scratch project folder, and no skill; save its stack choice and questions verbatim.
- [ ] **Step 2: With skills** — same scenarios with the `eggie-brainstorm` and `eggie-stack` SKILL.md paths given; scenario 4 (existing Django repo, a fixture made in the scratch dir) with `eggie-rules`; scenario 5 with `eggie-setup` + `eggie-plan`.
- [ ] **Step 3: Compare** against the spec's expected outcomes; fix wording where an agent did not comply; re-run that scenario.
- [ ] **Step 4: Record** the outcome in the PR description (what failed at baseline, what the skill changed, what was left).

---

### Task 8: Docs and PR

**Files:**
- Modify: `CLAUDE.md` (the skills bullet in *Layers*)

- [ ] **Step 1: Update CLAUDE.md** — in the `engine/install.sh` / skills bullets, say the skills folder now holds five skills, name them in one line, and note the structural test.
- [ ] **Step 2: Full suite** `TMPDIR=$PWD/.tmp python3 -m pytest -q` — all pass.
- [ ] **Step 3: Push and open the PR** to `main` with the spec link, the behaviour-check outcome, and the attribution lines; then run a separate review agent on it.
