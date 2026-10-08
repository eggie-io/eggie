# Guest CLI as a stdlib-only package shipped as a zipapp

Issue #59. Splits `runtime/cli/eggie.py` into a small package without changing what the `eggie`
command does, and keeps the guest install a single file.

## 1. Why

`runtime/cli/eggie.py` is one 628-line script holding five unrelated things: the API transport
with its job-poll and busy-retry loops, the project-folder rules (slug, folder lookup, overlay
directory permissions), the secret-name rules re-declared from the API, the `new`/`clone` flows,
and the argparse parser with its dispatch table. The "one file" shape was never a design goal;
it is what `install -m 755 cli/eggie.py /usr/local/bin/eggie` needed. Phase 1 grows the guest
CLI, and each new command lands in the same screen as everything else.

No argument-parsing library: the parser is ~45 lines of argparse for eight flat subcommands,
and the guest is a stock Ubuntu VM whose `install.sh` fails the whole install on any package it
cannot get. A dependency buys nothing here and adds a failure mode to every boot.

## 2. What stays fixed

- Every command, argument, message, exit code and output line. `tests/runtime/cli/` (seam tests
  against the real API app, path rules, secret rules, transport) passes with loader and
  import changes only.
- **Stdlib only**, and **imports neither `host/` nor `eggie_api`**; the shared names
  (`API_PORT`, `GUEST_ROOT`, `GUEST_PROJECTS`, `GUEST_TOKEN`, `GUEST_STACK`, `COMPOSE_FILE`,
  `API_UNCONFIGURED`, `VERIFY_PROJECT_ID`, the reserved secret names) stay re-declared and are
  held equal by `tests/test_constants_agree.py`.
- One executable at `/usr/local/bin/eggie`, mode 755, no interpreter options, no `PYTHONPATH`.
- Code moves verbatim with its comments.

## 3. Layout

```
runtime/cli/
  eggie_cli/
    __init__.py     re-exports the public surface (what tests and the loader use)
    constants.py    the re-declared seam values + DOCKER_GROUP, ALT_COMPOSE_FILES
    errors.py       EggieError, ApiError, JobFailed
    api.py          timeouts, START_STACK/RESTART_API, guidance, read_token, ApiClient,
                    default_client
    project.py      project_id_for, project_of, require_id, docker_gid, prepare_overlay_dir,
                    alt_compose_file, repo_name
    env.py          Env, run_git, project_here, require_project
    commands.py     start, print_project, cmd_up/status/logs/down/new/clone, new_folder
    secrets.py      the secret-name rules and cmd_secret_set/request/list/rm
    cli.py          parser, secret usage error, main (the EggieError → exit 1 boundary)
```

`runtime/cli/eggie.py` is deleted. The package name is `eggie_cli`: not `eggie` (the launcher's
name on PATH) and not `cli` (the zipapp root sits first on `sys.path`, so a top-level name must
not shadow a stdlib or common module — the lesson from #57's `http/`).

Module dependencies run one way: `cli → commands | secrets → env → api | project → errors |
constants`. Relative imports inside the package; nothing else.

`__init__.py` re-exports: every constant, `EggieError`, `ApiError`, `JobFailed`, `ApiClient`,
`read_token`, `START_STACK`, `RESTART_API`, `Env`, `project_id_for`, `project_of`, `require_id`,
`repo_name`, `main`. Tests reach the CLI through this surface only.

## 4. Install

`install.sh` step 9 replaces the `install` line with a build:

```bash
tmp="$(mktemp)"
python3 -m zipapp "$RUNTIME_DIR/cli" -m "eggie_cli.cli:main" -p "/usr/bin/env python3" -o "$tmp"
install -m 755 "$tmp" /usr/local/bin/eggie
rm -f "$tmp"
```

`zipapp` archives `runtime/cli/` (which holds only `eggie_cli/`), prepends the shebang, and
`-m` writes the `__main__` that calls `eggie_cli.cli:main`. The result is one self-contained
file the stock `python3` runs; `python3 -m zipapp` is in the standard library, so the install
gains no dependency. The `runtime/cli/` directory holds nothing but the package: anything added
there ships inside the executable.

`main()` keeps its `(argv=None, env=None) -> int` signature; the zipapp entry calls it with no
arguments and exits with its return value (zipapp's generated `__main__` does
`sys.exit(main())`).

## 5. Tests

- `tests/runtime/cli/loader.py`: `load()` puts `runtime/cli` on `sys.path` once and returns
  `importlib.import_module("eggie_cli")`; `GUEST_CLI` becomes the package directory. Every
  `cli.<name>` the tests use is on the barrel.
- `tests/runtime/cli/test_boundaries.py`:
  - the stdlib-only test walks every `*.py` under the package; absolute imports must be stdlib
    or `eggie_cli`, relative imports are allowed, and it asserts it scanned more than one file;
  - a new test asserts `runtime/cli/` contains only `eggie_cli/` (and no top-level entry that
    shares a name with `sys.stdlib_module_names`), because that directory is the zipapp root;
  - the `START_STACK`/`RESTART_API`/slug tests are unchanged.
- `tests/runtime/test_install_shell.py`: one new text assertion — step 9 builds the CLI with
  `python3 -m zipapp "$RUNTIME_DIR/cli"` and installs the result at `/usr/local/bin/eggie` with
  mode 755 (the way the other install-line tests pin their lines).
- `tests/test_constants_agree.py`: unchanged; `_public(load())` reads the barrel.
- No tests for moved code.

## 6. Docs

- `runtime/CLAUDE.md`: the `cli/eggie.py` bullet becomes `cli/eggie_cli/` — a stdlib-only
  package built into `/usr/local/bin/eggie` by `install.sh` with `zipapp`; the import rule and
  the constants-agreement note stay; add the "nothing but the package under `runtime/cli/`"
  rule.
- `runtime/install/CLAUDE.md`: step 9 mention, if it names the `install` line.
- Any other `cli/eggie.py` mention outside `docs/superpowers/` (grep).

## 7. Commits

One branch (`feature/59-guest-cli-package`), one PR, each commit green:

1. Create the package by moving the file's sections into the modules above; delete `eggie.py`;
   switch the loader and `test_boundaries.py`; suite green.
2. `install.sh` zipapp build + its text test; a local `python3 -m zipapp runtime/cli -m
   eggie_cli.cli:main -o /tmp/x && /tmp/x --help` smoke run recorded in the PR.
3. Docs.
