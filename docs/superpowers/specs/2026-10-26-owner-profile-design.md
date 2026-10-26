# Owner profile — let coding agents adapt to the user

`runtime/instructions/eggie.md` and the eggie-skills assume one owner: not technical, never asked
about technology. That is the right default and wrong for a developer who wants to pick the
stack. A small per-account file turns the assumption into a setting.

## The file: `~/.eggie/owner.md`

Per Linux account (instructions are installed per account already). Not in the project folder:
it describes the person, and a project file travels in git and push-to-kitchen.

```markdown
Level: developer

- Name: Sam. Prefers short answers.
```

- `Level:` is the first line, one of `just-results`, `explain`, `developer`.
- Everything after it is free notes the agent keeps (name, language, how they like answers).
- The agent creates and edits it; nothing in the installer writes or overwrites it.

## Asking

When the file is missing, the agent asks once, at the start of the first task, before anything
else — one message, plain words:

> Before we start, so I talk to you the right way:
> 1. Just show me results
> 2. Explain what you're doing as we go
> 3. I'm a developer — talk tech and let me make technical choices

Whatever the answer, it writes the file (no answer or "skip" → `just-results`) and says the
owner can change it any time by telling it. "Be more technical with me" and the like → the agent
updates `Level:` and confirms in one line.

## What the level changes

| | just-results | explain | developer |
|---|---|---|---|
| Words | plain, no tech terms | plain; a tech name gets a short plain gloss | tech terms fine |
| Technical decisions | agent decides, says what | agent decides, says what and why in a sentence | agent asks when the choice is real; owner's pick wins |
| Brainstorm | today's interview | today's interview | tech words fine; a named stack goes to *Stack wishes* |
| Stack | ladder | ladder, explained | asks once; owner-named stack wins, recorded as their choice, one warning |
| Plan | not shown | not shown | offered |

`just-results` is today's behaviour exactly. Eggie's own rules — `eggie up` only, no stray dev
servers, secrets via `eggie secret request`, `gh` via **Connect GitHub** — hold at every level.

## Changes

**`eggie` repo**
- `runtime/instructions/eggie.md`: replace "The user is not technical…" with: read
  `~/.eggie/owner.md` first; missing → ask (above); follow the level; missing level reads as
  `just-results`.

**`eggie-skills` repo**
- `eggie-setup`: "The owner is not technical: decide everything yourself" → follow the owner's
  level.
- `eggie-brainstorm`: the no-technology-words rule applies below `developer`. The brief gains
  a *Stack wishes* section (usually "None."); the stack question itself belongs to
  `eggie-stack`.
- `eggie-stack`: the anti-pattern "asking the owner to choose between technologies" applies
  below `developer`. A `developer` with no wish is asked once. A stack they name wins:
  *Ladder step* reads `Owner's choice: <stack>.`, *Rejected* holds the ladder's pick with one
  sentence on the cost; no further arguing. Below `developer`, *Stack wishes* is just a need.
- `eggie-plan`: offer the plan to a `developer`.
- `eggie-rules/assets/AGENTS.md`: "Talking to the owner" points at `~/.eggie/owner.md` instead
  of restating "no technical questions" — otherwise every project's `AGENTS.md` overrides the
  profile.

## Testing

No code changes on either side; the skills tests (names, hand-offs, bundled files) keep
guarding structure. Behaviour is checked by hand: the bakery idea and "set up this Laravel repo"
run with each level, and the transcript shows the wording, the questions asked and the
`docs/stack.md` written. A `developer` asking for Flask for a shop gets Flask, one warning and
`Owner's choice.`

## Out of scope

Editing the level in the console; storing it on the web-app account so it follows the owner to
a new VM or kitchen.
