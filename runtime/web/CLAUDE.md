# runtime/web/ — the browser console

React + Vite + TypeScript npm workspace, shipped as the `eggie-web` nginx image. Traefik routes
`localhost:<edge>/` here and `/api` to the API. A sibling of the API inside `runtime/` — never nest
it in the Python package. Not to be confused with `host/desktop/ui/` (the native window's local
screens); don't share code or assets with it.

## Commands (Node ≥ 22.22)

```bash
npm install
npm run dev          # Vite + in-browser mock API; ?scenario=<name> picks a state —
                     # the list is SCENARIOS in apps/console/src/mocks/handlers.ts
npm test             # Vitest (all workspaces)
npx vitest run apps/console/src/projects/view.test.ts   # one file
npm run typecheck
npm run build && npm run check-offline
```

The image build needs `--build-context fixtures=tests/fixtures` and
`--build-context agents=runtime/agents` (use `packaging/images/build.sh`);
a bare `docker build runtime/web` fails. Release versioning is in `runtime/CLAUDE.md`.

## Structure

- `packages/ui` — the kit: tokens, fonts, components, drawn from `docs/design/` (never from
  `host/desktop/ui`). Imports are held to React and its fonts by `packages/ui/test/boundary.test.ts`.
- `apps/console` — the app.
  - `/p/:id/secrets` — the Secrets page; `projects/secrets.ts`'s `nameProblem` mirrors the API's
    name rule. Sections: Requested (hint + value), Your secrets (write-only, typed values visible), Add. Mock: `?scenario=secrets`.
  - `boot/boot.ts` decides signed-in / signed-out / needs-update / not-answering from `/api/health`
    and `/api/session`. `api/version.ts`'s `SUPPORTED_API` is held to the API's `API_VERSION` by
    `tests/test_constants_agree.py`.
  - `projects/view.ts` maps the API's `status`/`problem`/`job`/`empty` to one screen state for both
    the list and the project page. `projects/slugify.ts` mirrors the API's `_slug`; both read
    `tests/fixtures/slugify-cases.json`.
  - `uploads/queue.ts` owns the chunked-upload protocol (resume at the API's offset, busy retry on
    the last chunk, hold on `disk_full`) with no React in it. One instance lives above the router in
    `uploads/QueueProvider.tsx`, so uploads continue across screens but stop when the page closes.
  - `imports/` — "From a folder" on the projects page: `plan.ts` drops `.git`/`node_modules`/… and
    names the project after the folder, `tar.ts` builds an uncompressed ustar+PAX Blob around
    references to the picked files, `send.ts` orders delete/create/upload per merge or replace
    mode with no React in it. One `POST /projects/{id}/files` request, no resume; the API unpacks
    it with Python's `tarfile`.
  - `desktop/desktop.ts` reads the `home=` address the desktop window adds to the handoff link (only
    `http://127.0.0.1:<port>`), which enables the shell's Home button and "open in browser".
  - `/welcome` and `/welcome/:id` — first-run onboarding, the same picker and guide with a "Check the
    connection" last step. `onboarding/onboarding.ts` decides: shown only when no agent is connected,
    the counter is empty and this browser's localStorage has no done/skipped mark. Mock: `?scenario=fresh`.
  - `/agents` and `/agents/:id` — the agent guide (the "Agents" tab). All content is in
    `runtime/agents/` (served at `/agent-guides/`; the Dockerfile copies it, a Vite plugin serves it in dev): `index.json` for order,
    `<id>/agent.json` with `windows` and `mac` blocks, an optional `card` of fields to copy, steps with an HTML
    `body` and a `media` list (screenshots and videos; more than one makes a slider).
    `src/agents/catalog.ts` validates it; `content.test.ts` fails on a file that doesn't parse or
    names a missing media file. `src/agents/rich.ts` parses the body into a tree of allowed
    tags that `RichText.tsx` renders as elements — no `innerHTML`.

## Hard rules

- **Works offline.** `scripts/check-offline.mjs` fails the image build on any load from another host.
- **Assets ship as files.** The CSP refuses `data:` fonts, hence `build.assetsInlineLimit: 0` in
  `apps/console/vite.config.ts`.
- **Open external addresses with `desktop.ts`'s `openExternal`** (an anchor click), never
  `window.open` — WKWebView hands only link activations to the system browser.

## Things that will bite you

- `apps/console/src/projects/slugify.test.ts` reaches `tests/fixtures/slugify-cases.json` by
  counting `../` segments from its own location, and that count must agree with `Dockerfile`'s
  `WORKDIR` (and the `COPY --from=fixtures` destination). Changing one without the other passes
  `npm test` in the checkout and fails only inside the image build.
- `runtime/agents/` is outside this workspace: the image needs `--build-context agents=runtime/agents`
  (use `packaging/images/build.sh`), and `content.test.ts` reaches it with five `../`, which
  matches `/runtime/agents` in the image only because `WORKDIR` is `/runtime/web`.
- In a `.module.css` file, CSS Modules renames animation names to local ones, and the shared
  `om-*` keyframes in `tokens.css` have no local twin. Write `animation: global(om-spin) …`
  (no colon). A bare name silently never runs; `packages/ui/test/animations.test.ts` checks.
- The first `file.size` of each picked file is a blocking disk stat (about half a millisecond in
  Chrome): 40k files freeze the page for 15 s. `imports/plan.ts` reads sizes in batches that yield.
- Don't read a picked folder into the page to build an upload. Gzipping 1.8 GB in the page took
  36 s and held the whole archive in the browser process; a Blob with the `File`s as parts is
  built at once and streamed from disk (180 MB peak, 5 s).
- Upload memory and progress can't be measured in the dev app: MSW's service worker reads every
  request body itself (7 GB for a 1.8 GB upload) and no upload progress or `load` event fires.
  Measure from a page that never booted the app. Playwright's `setInputFiles(directory)` and
  `fileChooser.setFiles()` also hand the page in-memory copies; only `setInputFiles(path)` of a
  single file is disk-backed like a real pick.
- A file input's `cancel` event bubbles, and `Modal`'s `<dialog>` closes itself on `cancel`.
  Stop it at the input, or dismissing the folder picker closes the whole dialog.
- A file input's `files` is a live list: resetting `value` to let the same pick fire again empties
  it, so copy the files out first. The folder picker in `ImportFolderModal` once lost every pick to this.
- nginx's `/agent-guides/` location has no SPA fallback, so its path must never be a console route
  prefix: `/agents/` once turned every guide reload into a bare 404. `content.test.ts` checks.
