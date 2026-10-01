# Omelet web UI — part B: shell, kit, image and sign-in

Part B of four (spec 2026-09-21-web-ui-agent-prerequisites-design.md §1). The
decisions in that spec's §1 bind this one; its §8 "B" list is the input. Design
board: `docs/design/omelet-web-ui.dc.html` — frame 15, frame 16's layout, frame
01's top bar, and the component strip.

## 1. Outcome

When B is done, the VM serves a page at `localhost:39080` that:

- signs in through the desktop's "Open Omelet" handoff, or shows screen 15
  ("We've lost track of you") when it cannot;
- shows "needs an update" when the agent speaks another `api` number, and
  "isn't answering" when the agent does not answer;
- once signed in, shows the app shell and a placeholder "Your projects" page
  that lists each project's `id` from `GET /api/projects` — proof that the cookie
  works end to end; part C replaces it;
- serves `/kit`, a gallery of every kit component in both themes, for visual
  review of a deployed image.

Decisions made while designing B:

- **Theme** follows the OS (`prefers-color-scheme`) and switches live. No
  toggle.
- **Dev loop** is mock only: `npm run dev` runs against an in-browser fake of
  `/api` (MSW). The real pairing is checked by deploying the image into a VM.
- **Styling** is plain CSS: tokens as custom properties, one CSS Module per
  component. No Tailwind, no CSS-in-TS.
- **Libraries**: `react-router` (C needs `/projects/:id`), TanStack Query (C
  and D poll), `@fontsource` for the three fonts, Vitest, MSW.

## 2. Workspace and kit

```
web/
  package.json            npm workspaces; scripts: dev, build, test, typecheck
  packages/ui/            the kit — no app knowledge, no fetch
    src/tokens.css        light in :root, dark in @media (prefers-color-scheme: dark)
    src/fonts.ts          @fontsource imports, the only font source
    src/components/
    src/index.ts          the barrel
  apps/console/
    src/api/              fetch wrapper, ApiError, SUPPORTED_API
    src/boot/             boot() — §3
    src/shell/            top bar + page frame
    src/screens/          SignedOut, NeedsUpdate, NotAnswering, Projects, Kit
    src/mocks/            MSW handlers and scenarios
  scripts/check-offline.mjs
  Dockerfile, nginx.conf
```

**The kit consumed as source.** `packages/ui` has no build step; the console
imports it through the workspace and Vite compiles both. When the kit moves to
its own repository, a library build is added there.

**Tokens** are the board's `[data-theme]` block, moved to `:root` and to the
`prefers-color-scheme: dark` media query unchanged: desk, cream, surface,
surface-2, line, line-2, ink, ink-2, ink-3, yolk, yolk-deep, yolk-soft, basil,
basil-soft, paprika, paprika-soft, cold, cold-soft, shadow. The keyframes
(`om-spin`, `om-bob`, `om-zzz`, `om-bar`, `om-blink`, `om-glow`, `om-sizzle`) go
in the same file. Motion stops under `prefers-reduced-motion: reduce`.

**Fonts**: Bricolage Grotesque (headings, variable 400–800), Hanken Grotesk
(body, 400–700), IBM Plex Mono (400, 500), from `@fontsource`, bundled into the
build. Nothing loads from Google Fonts: the page must work offline.

**Components**, all from the board, none lifted from `host/desktop/ui`:

| Component | Variants / behaviour |
|---|---|
| `Button` | primary (yolk, lifts on hover), secondary (surface, line-2 border), quiet (transparent, line border), danger (paprika-soft), disabled; sizes `md` and `lg` |
| `StateBadge` | running, stopped, starting (spinner, the only one that moves), wrong; takes a state, not a project |
| `SyncMarker` | synced (with a "just now" label), offline; the app does not render it until sync exists |
| `PromptCard` | title bar, `<pre>` body, copy button → "Copied. It's yours." for 2.6 s |
| `Modal` | focus trapped, Esc and backdrop close, returns focus to the opener |
| `RowCard` | the list-row surface with its three accents: plain, attention (yolk-soft), trouble (paprika-soft ring) |
| `ProgressBar` | determinate, plus the indeterminate `om-bar` sweep |
| `Collapsible` | `<details>`-based, board's hidden marker |
| `Egg` | the logo; yolk and cold (signed-out) variants |
| `Notice` | the info line with an icon (frame 16's footer, frame 01's folder hint) |

The kit contains only what the board shows or part A's §8 names. It holds no
data logic: mapping agent payloads to states is C's job.

**Layout.** Fluid, with two densities from the board: under 1040 px wide,
frame 01's (52 px top bar, 22 px side padding); from 1040 px, frame 16's (54 px,
32 px). No max content width — the board draws 1200 as "the same page with
elbow room".

**The shell** is the top bar (Egg, "Omelet") over a scrolling content area,
with routes `/` (Projects placeholder) and `/kit`; any other path renders the
placeholder too, until C adds its routes.

## 3. Start-up and sign-in

```
load
 ├─ GET /api/health                     (open, no cookie)
 │    no answer / non-2xx ─────────────► notAnswering
 │    api ∉ SUPPORTED_API ─────────────► needsUpdate(agentApi)
 ├─ a handoff code was taken from the URL?
 │    yes: POST /api/session {"code": <code>}
 │           200 ───────────────────────► signedIn
 │           401 handoff_invalid ─► continue to GET /api/session below;
 │                                  if that is 401 too → signedOut("handoff_spent")
 │           anything else ─────────────► notAnswering
 └─ GET /api/session
      200 ─────────────────────────────► signedIn
      401 not_signed_in ───────────────► signedOut("not_signed_in")
      401 session_expired ─────────────► signedOut("session_expired")
      anything else ───────────────────► notAnswering
```

`takeHandoff(location, history)` reads `#handoff=<code>` and removes the hash
with `history.replaceState()` synchronously, before anything is awaited, and
the app keeps the code in a ref that the first boot empties. A reload never
resends a spent code, and React's StrictMode running start-up twice in
development never posts the same code twice. `boot({fetch, handoff})` is a
plain async function returning one of the four results; React renders the
result and holds no start-up logic. A code whose boot ended in `notAnswering`
is not retried: it lives 60 seconds, and a fresh one is one click away.

**API client.** A `fetch` wrapper with `credentials: "same-origin"`. It never
sets or strips `Origin`: the browser sends it on every non-GET, and the agent
refuses a non-GET without it. A non-2xx response becomes
`ApiError(code, message, status)` from the agent's `{"error": {"code",
"message"}}` body; a body in any other shape becomes `code: "unexpected"`; a
network failure becomes `code: "unreachable"`, status 0.

**Losing the session mid-use.** The TanStack Query client has one error handler:
a 401 with `not_signed_in` or `session_expired` from any query or mutation
switches the app to SignedOut. Screens never handle it. Other errors stay with
the screen that made the request.

**Screens.**

- **SignedOut (frame 15)**, with the `omelet://` scope cut applied: the board's
  "Open the Omelet app" button becomes text — "Open the Omelet app on your
  desktop and press *Open Omelet*." — and "Try again" becomes the primary button,
  re-running the session check. The page also re-checks on its own whenever the
  tab becomes visible again (`visibilitychange`): the desktop opens a new tab,
  and the old one recovers by itself. For `handoff_spent` the lead line says
  the link was already used or ran out. The footer line stays: "Your projects
  carried on the whole time. Nothing stopped, nothing was lost."
- **NeedsUpdate.** Frame 15's layout with the yolk Egg: the agent and this page
  are from different releases; update Omelet from the desktop app. Shows both
  `api` numbers in small mono text for support.
- **NotAnswering.** Same layout, cold Egg: Omelet's service inside the VM isn't
  answering. Retries on its own every 5 s and offers "Try again"; recovers
  into a full boot.
- **Projects placeholder.** "Your projects", one `RowCard` per project `id`
  (the agent has no separate name) from `GET /api/projects`. No actions.
- **Kit.** Every component and variant, rendered twice side by side under
  forced light and dark tokens (`.om-theme-light` / `.om-theme-dark` wrapper
  classes re-declaring the values). Density follows the viewport, so the
  880/1200 layouts are reviewed by resizing the window.

No sign-out button: the board has none, and part A leaves sign-out's dead-cookie
gap open (A §8). The client exposes `signOut()` for later.

**`SUPPORTED_API`** lives in `apps/console/src/api/version.ts` as
`export const SUPPORTED_API = [1];`. `tests/test_constants_agree.py` reads it
with a regex and asserts the agent's `API_VERSION` is in it.

**Mocks.** MSW handlers for `/api/health`, `/api/session` (GET/POST) and
`/api/projects`, with scenarios picked by `?scenario=`: `ok` (default),
`expired`, `handoff-spent`, `old-agent`, `down`, and `lost-mid-use` (signed
in, then `/api/projects` answers `session_expired`). MSW is started only by
`npm run dev` and never reaches the production bundle: its worker lives in a
dev-only public folder, and `check-offline.mjs` also fails a `dist/` that
contains `mockServiceWorker.js`.

## 4. Image, stack and release

**`web/Dockerfile`**, build context `web/`:

1. `FROM --platform=$BUILDPLATFORM node:24-alpine` — `npm ci`, typecheck,
   `npm test`, `vite build` for `apps/console`, then
   `scripts/check-offline.mjs`, which fails the build when anything in `dist/`
   makes the browser load from another host: a CSS `url()` or `@import`, or an
   HTML `src`/`href`, that is absolute (`http:`, `https:` or `//`). Strings in
   the JS bundle are not scanned — React's own bundle carries
   `https://react.dev/errors/` and the SVG namespaces, which are never fetched —
   and the CSP's `default-src 'self'` blocks any runtime fetch to another host.
2. `FROM nginxinc/nginx-unprivileged:alpine` — copies `dist/`, serves it on
   8080 as a non-root user.

The output is static, so the image is built for `linux/amd64` and `linux/arm64`
with `docker buildx` at no extra cost. The agent image is still built for one
architecture only.

**`web/nginx.conf`:**

- `try_files $uri /index.html`, so `/projects/x` survives a reload.
- `/assets/*`: `Cache-Control: public, max-age=31536000, immutable` (the file
  names are content-hashed); `index.html`: `no-cache`, so a new image takes
  effect on the next load.
- `Content-Security-Policy: default-src 'self'; img-src 'self' data:;
  style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none';
  form-action 'none'; object-src 'none'`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `server_tokens off`. `frame-ancestors 'none'` matters: project apps on
  `*.<domain>` share the browser and must not frame the console.

**`engine/stack.yml`** gains:

```yaml
  web:
    image: ${OMELET_WEB_IMAGE:-ghcr.io/omelet-app/omelet-web:0.2.0}
    restart: always
    networks:
      - edge
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.omelet-web.rule=Host(`localhost`) || Host(`127.0.0.1`)"
      - "traefik.http.routers.omelet-web.entrypoints=web"
      - "traefik.http.routers.omelet-web.priority=10"
      - "traefik.http.services.omelet-web.loadbalancer.server.port=8080"
```

No published ports. `install.sh` needs no change: it pulls and starts every
service in the file, and its pull-failure messages already say "the Omelet
images".

**Release.** The web image carries the agent's version; A and B ship together
as 0.2.0 (no engine tag exists past 0.1.0). `CLAUDE.md`'s release steps gain:

```
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/omelet-app/omelet-web:X.Y.Z --push web/
```

and the web tag joins the list of values bumped together. `CLAUDE.md` also
gains a `web/` bullet under Architecture and the `npm` commands under Commands.
`.gitignore` gains `node_modules/` and `web/**/dist/`.

## 5. Testing

Vitest, run with `npm test` in `web/`. Pytest never calls npm.

- **`boot()`**: one test per outcome in the §3 diagram, including a spent handoff
  with a live cookie resolving to `signedIn`.
- **`takeHandoff()`**: returns the code and removes the hash; leaves history
  alone when there is none.
- **API client**: an agent error body, a body that isn't JSON, and a refused
  connection — the seam with the agent's contract.
- **Kit boundary**: scans `packages/ui/src` imports and fails when the kit
  imports from `apps/console`, `@tanstack/react-query`, `react-router` or `msw`,
  or calls `fetch`. It keeps the kit movable to its own repository.

Python side:

- `tests/test_constants_agree.py`: the stack's web image tag equals the agent's
  `__version__`; `SUPPORTED_API` contains `API_VERSION`.
- `tests/engine/test_stack_yml.py`: the `web` service exists, joins `edge`,
  publishes no ports, and its router's priority is below `omelet-api`'s.

Not tested, noted in the PR: component rendering (no snapshots of the kit),
the layout breakpoint, PromptCard's timer, MSW handlers. These are glue or
visual; the `/kit` page covers them by eye.

## 6. Branch

`feature/web-shell`, from `feature/agent-web-ui`: B needs A's routes and A is
not merged. The PR targets `feature/agent-web-ui`, or `main` if A lands first.
