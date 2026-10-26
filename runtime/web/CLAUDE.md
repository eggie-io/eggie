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

- `packages/ui` — the kit: tokens, fonts, components, drawn from the Eggie design system
  (claude.ai/design project "Omelet: Linux VM Setup UI": `Eggie Host + Console v3.dc.html` and
  `_ds/eggie-console-design-system-…`), never from `host/desktop/ui` or the old `docs/design/` board.
  Imports are held to React and its fonts by `packages/ui/test/boundary.test.ts`.
  - Every icon is `<Icon name=…>`; the glyphs (the design system's 24px stroke set) are paths in
    `src/icons.ts`, so a new one is added there, not drawn inline. `Button`, `IconButton`, `Badge`
    and `Notice` take an icon by name.
  - `src/tokens.css` holds every colour, type, space and shape token, light on `:root` and dark
    twice (OS setting and the `.om-theme-dark` gallery wrapper). `packages/ui/test/tokens.test.ts`
    fails on a `var(--x)` that no CSS declares, so a renamed token can't silently resolve to nothing.
    No hex colours in TSX or module CSS (the QR code's black-on-white is the one exception).
- `apps/console` — the app. `/kit` is the component gallery in both themes.
  - `/p/:id/*` pages render inside `screens/project/ProjectLayout.tsx`: it owns the project menu
    (`ProjectMenu.tsx`; its Secrets and Public address notes come from `menu.ts`'s `menuNotes`) and
    the Public address and Delete dialogs (Analyze is its own page, `AnalyzePage.tsx`,
    `/p/:id/analyze`). Overview, Files and Secrets render content only, starting with `TitleRow.tsx`. An unknown or deleted id still gets the layout, with a
    bare menu. `projects/color.ts`'s `projectColor` gives a project its tile colour, stable per id.
  - `/p/:id/secrets` — the Secrets page; `projects/secrets.ts`'s `nameProblem` mirrors the API's
    name rule. Sections: Requested (name with its hint, value), Your secrets (write-only, typed values visible), Not used by the latest push, Add. While `start_when_ready` is set a notice offers "Start without them" (the ordinary `up`). Start on a project with `secrets_requested > 0` calls `start-when-ready` and lands here. Mock: `?scenario=secrets`, `kitchen-waiting-secrets`.
  - `push/push.ts` decides the Push dialog (kitchen rows, data default, confirmations, floating-tag warnings, menu state, secrets link) with no React; `screens/project/PushModal.tsx` renders it and shows the API's messages as they come (the server renders the error copy). Mock: `?scenario=push-no-account`, `push-offline`, `push-no-kitchens`, `push-kitchens`, `push-running`, `push-done-secrets`, `push-rate-limited`, `kitchen-waiting-secrets`.
  - Instance switcher: `instances/instances.ts` decides the rows (`switcherEntries`: owner check, then status, then signed-out; "This computer" only on the local VM, never clickable), `shell/InstanceSwitcher.tsx` is the top-bar popover (fetches `GET /api/instances` when opened; a pick navigates to `openKitchenUrl`, through `openExternal` in the desktop window). Mock: `?scenario=kitchens`, `no-kitchens`, `kitchens-down`.
  - `boot/boot.ts` carries `site` from `/api/health` on its result (App keeps the last one) into `SiteContext` (`useSite()`, and `webApp` likewise into `WebAppContext`/`useWebApp()`; the initial state comes from the page address via `isLocalHost`, so `undefined` occurs only on a non-local address before the first boot answer); on a site the signed-out screen redirects (`window.location.replace`) to `<web_app>/kitchens/open?host=…` via `site/signIn.ts`, or shows a Sign in button after a refused code (`handoff_spent`, so a failing redeem cannot loop), the sign-in screen says the server lost its sign-in (no button), the project menu's Public address item and sign-out are hidden, and the agents card drops rows for facts that do not exist (`key_file: null`). Mock: `?scenario=site`, `?scenario=site-signed-out`.
  - `boot/boot.ts` decides signed-in / signed-out / needs-update / not-answering from `/api/health`
    and `/api/session`. `api/version.ts`'s `SUPPORTED_API` is held to the API's `API_VERSION` by
    `tests/test_constants_agree.py`.
  - `projects/view.ts` maps the API's `status`/`problem`/`job`/`empty` to one screen state for both
    the list and the project page (`waiting`/`setup` drive `HandoffCard.tsx`; mock `?scenario=handoff-new`,
    `-github`, `-folder`). `projects/slugify.ts` mirrors the API's `_slug`; both read
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
    `<id>/agent.json` with `windows`, `mac` and `cloud` blocks, an optional `card` of fields to copy, steps with an HTML
    `body` and a `media` list (screenshots and videos; more than one makes a slider).
    `src/agents/catalog.ts` validates it; `content.test.ts` fails on a file that doesn't parse or
    names a missing media file. `screens/agents/AgentMark.tsx` shows an agent's icon, or its
    initials when the image fails to load. `src/agents/rich.ts` parses the body into a tree of allowed
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
- `runtime/agents/claude-code/icon.webp` is 6 bytes shorter than its RIFF header says (likely cut by
  an old `text eol=lf` rule before images were marked binary), so browsers can't decode it and the
  picker shows "CC". Re-export it from the source image; patching the bytes won't bring it back.
