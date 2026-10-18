# Kitchen Sign-in and Instance Switcher Implementation Plan

> **Superseded in part (2026-10-18):** the desktop window no longer browses kitchens. A kitchen picked
> there opens in the system browser, "This computer" is only the local VM's label, and the `home`
> hand-off and the `#console` desktop route were removed. See `docs/site-mode.md` for what is current.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A kitchen console with no session signs the person in through the web app, and every signed-in console has a top-bar switcher between the local VM (desktop only) and the account's kitchens.

**Architecture:** The web app gets one page, `/kitchens/open?host=…&home=…`, that mints a console code with the person's web session and redirects to the kitchen. The kitchen console redirects there when it has no session; the switcher lists kitchens through the runtime API (device token, `GET /v1/instances`) and links to the same page. The desktop gets an optional `#console` route for "This computer".

**Tech Stack:** FastAPI + pytest (`runtime/eggie_api`), React + Vite + Vitest + TanStack Query (`runtime/web`), React + TanStack Router + Orval hooks + Vitest (`eggie-web/src/frontend`), plain JS (`host/desktop/ui`).

**Spec:** `docs/superpowers/specs/2026-10-16-instance-switcher-design.md`

## Global Constraints

- No Eggie service backend change. `/v1/instances/{id}/console` stays web-session-only.
- No redirect target is ever taken from a URL parameter: the web app page redirects only to the `url` the service returns for a kitchen from the user's own list.
- Desktop home accepted only as `http://127.0.0.1:<port>` with no userinfo, on both the web app page and the console.
- `/api/instances` is console-only (mounted at `/api` alone). No `API_VERSION` bump.
- Navigation to the web app page uses `location.assign` (switcher) or `location.replace` (signed-out redirect, web app page), never `openExternal`.
- Copy pinned by the spec: "This computer", "Owned by a teammate", "Starting…", "Failed, see the web app", "Set up again in the web app", "Kitchens are created in the Eggie web app", "This kitchen isn't in your account", "Only the kitchen's owner can open its console".
- Two repos: `eggie` on `feature/48-instance-switcher`; `eggie-web` (`../eggie-web`) on a new `feature/48-kitchen-open-page` from `origin/main`. In `eggie-web`, never stage `README.md` or `eggie-laptop_ed25519` (unrelated local changes).
- Python tests: `TMPDIR=/home/ihor/tmp python3 -m pytest …` from the `eggie` root. Console: `npm test` / `npm run typecheck` in `runtime/web`. Web app: `export PATH=$HOME/.nvm/versions/node/v26.8.2/bin:$PATH` then `pnpm check` in `eggie-web/src/frontend`.

## Review Focus

1. A kitchen whose redeem keeps failing (service down, revoked) must not bounce between kitchen and web app forever: `handoff_spent` shows a button, never auto-redirects. Pinned in Task 3.
2. `host` that differs only in case from `console_hostname` (typed address, bookmark) must still open the kitchen. Pinned in Task 1.
3. A service URL that already carries a fragment (`#handoff=…`) must keep `handoff` intact when `home` is added. Pinned in Task 1.
4. The switcher on a kitchen in a plain browser must not offer "This computer" (it could only reach a signed-out local page). Pinned in Task 4.
5. `GET /v1/instances` entries missing `console_hostname` (half-provisioned rows) must be dropped, not rendered as dead links. Pinned in Task 2.

---

### Task 1: Web app — `/kitchens/open` page (eggie-web)

**Files:**
- Create: `eggie-web/src/frontend/src/modules/instances/lib/open-kitchen.ts`
- Create: `eggie-web/src/frontend/src/modules/instances/lib/open-kitchen.test.ts`
- Create: `eggie-web/src/frontend/src/modules/instances/pages/open-kitchen-page.tsx`
- Modify: `eggie-web/src/frontend/src/modules/instances/routes.tsx`
- Modify: `eggie-web/src/frontend/CLAUDE.md` (module table: `instances` gains `/kitchens/open`)

**Interfaces:**
- Produces (URL contract the runtime relies on): `GET <web app>/kitchens/open?host=<console_hostname>[&home=<encoded url>]`.
- Produces: `openKitchenDecision(instances: InstanceOut[], host: string): {kind: "notFound"} | {kind: "notOwner", instance} | {kind: "notReady", instance} | {kind: "ready", instance}`; `withDesktopHome(url: string, home: string | undefined): string`.

- [ ] **Step 1: Branch**

```bash
cd ../eggie-web && git fetch origin && git switch -c feature/48-kitchen-open-page origin/main
```
Expected: switched; `git status` still shows `README.md` modified and the key file untracked — leave both alone.

- [ ] **Step 2: Write the failing tests** in `open-kitchen.test.ts`

```ts
const k = (over: Partial<InstanceOut>) => ({ id: "i1", console_hostname: "foo.eggie.io", status: "running", signed_out: false, is_owner: true, display_name: "Foo", ...over }) as InstanceOut;

it("finds the kitchen by host, ignoring case", () =>
  expect(openKitchenDecision([k({})], "FOO.eggie.io")).toEqual({ kind: "ready", instance: k({}) }));
it("is notFound for a host outside the list", () =>
  expect(openKitchenDecision([k({})], "bar.eggie.io").kind).toBe("notFound"));
it("is notOwner for a teammate's kitchen", () =>
  expect(openKitchenDecision([k({ is_owner: false })], "foo.eggie.io").kind).toBe("notOwner"));
it.each(["provisioning", "booting", "failed"])("is notReady while %s", (status) =>
  expect(openKitchenDecision([k({ status })], "foo.eggie.io").kind).toBe("notReady"));
it("is notReady when signed out", () =>
  expect(openKitchenDecision([k({ signed_out: true })], "foo.eggie.io").kind).toBe("notReady"));

it("adds a loopback home to an existing fragment", () =>
  expect(withDesktopHome("https://foo.eggie.io/#handoff=abc", "http://127.0.0.1:5123"))
    .toBe("https://foo.eggie.io/#handoff=abc&home=http%3A%2F%2F127.0.0.1%3A5123"));
it("starts a fragment when there is none", () =>
  expect(withDesktopHome("https://foo.eggie.io/", "http://127.0.0.1:5123"))
    .toBe("https://foo.eggie.io/#home=http%3A%2F%2F127.0.0.1%3A5123"));
it.each(["https://evil.example", "http://localhost:5123", "http://127.0.0.1", "http://u:p@127.0.0.1:1", "javascript:alert(1)"])(
  "drops a home that is not http://127.0.0.1:<port>: %s", (home) =>
  expect(withDesktopHome("https://foo.eggie.io/#handoff=abc", home)).toBe("https://foo.eggie.io/#handoff=abc"));
it("leaves the url alone without a home", () =>
  expect(withDesktopHome("https://foo.eggie.io/#handoff=abc", undefined)).toBe("https://foo.eggie.io/#handoff=abc"));
```

- [ ] **Step 3: Run to verify they fail**

Run: `pnpm test src/modules/instances/lib/open-kitchen.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 4: Implement `open-kitchen.ts`**

Lookup is `console_hostname.toLowerCase() === host.toLowerCase()`; owner check before readiness; ready = `status === "running" && !signed_out`. `withDesktopHome` uses `URL` to validate home (`protocol === "http:"`, `hostname === "127.0.0.1"`, non-empty `port`, empty `username`/`password`) and appends with `encodeURIComponent`.

- [ ] **Step 5: Run to verify they pass**

Run: `pnpm test src/modules/instances/lib/open-kitchen.test.ts`
Expected: PASS.

- [ ] **Step 6: Page and route**

`open-kitchen-page.tsx`: reads `host` and `home` from validated search (`z.object({ host: z.string().min(1), home: z.string().optional() })`); `useListInstances()`; on `ready`, calls the `openInstanceConsole` mutation once (guard against StrictMode double-fire with a ref) and `window.location.replace(withDesktopHome(data.url, home))`. Other outcomes: `ErrorState` with the spec's copy and a link to `/kitchens` (notFound) or `/kitchens/$instanceId` (notOwner, notReady — notReady describes the state via `lib/instance-status`). Mutation error: show `message`, a Retry button. While working: "Opening <display_name>…".
`routes.tsx`: `openKitchenRoute` (`path: "/kitchens/open"`, parent `authenticatedRoute`, `validateSearch`), added to `authenticatedRoutes` **before** `instanceRoute`.

- [ ] **Step 7: Verify routing and the whole check**

Run: `pnpm check`
Expected: typecheck, lint, format, tests, check:api all pass.
Then `pnpm dev`, open `http://localhost:5173/kitchens/open?host=nope.eggie.io` signed out: lands on `/login?redirect=…`; after login, shows "This kitchen isn't in your account" (not the kitchen detail page — proves the static route wins).

- [ ] **Step 8: Commit**

```bash
git add src/frontend/src/modules/instances src/frontend/CLAUDE.md
git commit -m "instances: /kitchens/open signs a person into their kitchen's console"
```

---

### Task 2: Runtime API — `web_app` in health, `GET /api/instances`

**Files:**
- Modify: `runtime/eggie_api/config.py` (`web_url`, env `EGGIE_WEB_URL`, default `"https://app.eggie.io"`)
- Modify: `runtime/eggie_api/services/system.py` (health gains `"web_app": config.web_url`)
- Modify: `runtime/eggie_api/infra/cloud.py` (`list_instances(token)`)
- Create: `runtime/eggie_api/services/instances.py`
- Create: `runtime/eggie_api/rest/routers/instances.py`
- Modify: `runtime/eggie_api/rest/app.py` (mount `instances.build_console(services)` at `/api` only)
- Modify: `runtime/eggie_api/wiring.py` (`instances: Instances` on `Services`, built from `account`, `cloud`, `config.site_host`)
- Modify: `tests/runtime/api/fake_cloud.py` (`list_instances(token)`)
- Create: `tests/runtime/api/test_instances.py`
- Modify: `tests/runtime/api/test_site.py` (health `web_app`)
- Modify: `runtime/eggie_api/CLAUDE.md` (feature area entry)

**Interfaces:**
- Produces: `GET /health` and `GET /api/health` → `{..., "web_app": "https://app.eggie.io"}`.
- Produces: `GET /api/instances` → `{"instances": [{"id": str, "display_name": str, "console_hostname": str, "status": str, "signed_out": bool, "is_owner": bool, "current": bool}]}`; errors `409 not_signed_in`, `503 cloud_unavailable`, `502 cloud_error`.
- Produces: `Instances(account, cloud, site_host: str | None).list() -> dict`.

- [ ] **Step 1: Write the failing tests** (`test_instances.py`, service built like `test_api_public.build`: real `Account` over a temp `Repos` with a stored token, `FakeCloud`)

```python
ROW = {"id": "i1", "name": "foo", "display_name": "Foo", "status": "running", "step": None,
       "console_hostname": "foo.eggie.io", "site_hostname": "foo.eggie.space",
       "signed_out": False, "is_owner": True, "ipv4": "1.2.3.4"}  # plus the other InstanceOut fields

def test_lists_kitchens_in_service_order_with_only_the_fields_the_switcher_needs(...):
    out = instances(cloud=FakeCloud(list_instances=[[ROW, {**ROW, "id": "i2", "console_hostname": "bar.eggie.io"}]])).list()
    assert out == {"instances": [
        {"id": "i1", "display_name": "Foo", "console_hostname": "foo.eggie.io", "status": "running",
         "signed_out": False, "is_owner": True, "current": False},
        {... "id": "i2", "console_hostname": "bar.eggie.io", "current": False}]}

def test_marks_current_only_the_kitchen_this_server_is(...):    # site_host="foo.eggie.io" → i1 current, i2 not
def test_drops_rows_without_a_console_hostname_or_id(...):       # {**ROW, "console_hostname": ""}, {**ROW, "id": None} dropped
def test_an_empty_account_lists_nothing(...):                    # [] → {"instances": []}
def test_not_signed_in_is_a_409(...):                            # no token → Conflict code "not_signed_in"
def test_service_down_is_a_503(...):                             # CloudUnavailable → Unavailable "cloud_unavailable"
def test_a_service_error_is_a_502_with_its_message(...):         # CloudError("x", "boom", 500) → Upstream "cloud_error", message "boom"

def test_instances_is_reachable_from_the_console_only(env):
    assert console(env.app, env.client).get("/api/instances").status_code != 404
    assert env.client.get("/instances").status_code == 404
```
And in `test_site.py`:
```python
def test_health_names_the_web_app(env):
    assert env.client.get("/health").json()["web_app"] == "https://app.eggie.io"
def test_web_app_comes_from_the_environment():
    assert ApiConfig.from_env({"EGGIE_WEB_URL": "http://localhost:5173"}).web_url == "http://localhost:5173"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_instances.py tests/runtime/api/test_site.py -q`
Expected: FAIL (import error / missing key).

- [ ] **Step 3: Implement** config, health, `Cloud.list_instances` (`GET /v1/instances`), `FakeCloud.list_instances`, `Instances.list` (through `account.authed`; translate per Interfaces), router `build_console` with `GET /instances`, mount and wiring.

- [ ] **Step 4: Run the API suite**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api -q` then the full `TMPDIR=/home/ihor/tmp python3 -m pytest -q`
Expected: all pass (layering, route sweep and constants tests included).

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api tests/runtime/api
git commit -m "api: list the account's kitchens for the console, name the web app in health"
```

---

### Task 3: Console — a kitchen with no session goes to the web app

**Files:**
- Create: `runtime/web/apps/console/src/site/signIn.ts`
- Create: `runtime/web/apps/console/src/site/signIn.test.ts`
- Modify: `runtime/web/apps/console/src/boot/boot.ts` (result carries `webApp?: string | null` from health, alongside `site`)
- Modify: `runtime/web/apps/console/src/site/SiteContext.tsx` (add `WebAppContext` / `useWebApp()`, same keep-last rule as `nextSite`)
- Modify: `runtime/web/apps/console/src/App.tsx` (provide `WebAppContext`)
- Modify: `runtime/web/apps/console/src/screens/SignedOut.tsx`
- Modify: the site `needsAccount` screen (the `SignIn` screen's site branch) — add a link to `<web_app>/kitchens`
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (`site` scenario's health gains `web_app`; scenario `site-signed-out`)
- Modify: `docs/site-mode.md` ("Account and sign-in", "What the console shows", contract: `/kitchens/open`)
- Modify: `runtime/web/CLAUDE.md` (site bullet)

**Interfaces:**
- Consumes: `/api/health`'s `web_app` (Task 2); the web app page URL contract (Task 1).
- Produces: `openKitchenUrl(webApp: string, host: string, home: string | null): string | null` (null when `webApp` is unusable); `siteSignedOutAction(reason: SignedOutReason): "redirect" | "button"`; `useWebApp(): string | null | undefined`.

- [ ] **Step 1: Write the failing tests** (`signIn.test.ts`)

```ts
it("builds the web app page link with the host", () =>
  expect(openKitchenUrl("https://app.eggie.io", "foo.eggie.io", null))
    .toBe("https://app.eggie.io/kitchens/open?host=foo.eggie.io"));
it("carries the desktop home", () =>
  expect(openKitchenUrl("https://app.eggie.io/", "foo.eggie.io", "http://127.0.0.1:5123"))
    .toBe("https://app.eggie.io/kitchens/open?host=foo.eggie.io&home=http%3A%2F%2F127.0.0.1%3A5123"));
it("accepts a plain-http localhost web app for development", () =>
  expect(openKitchenUrl("http://localhost:5173", "foo.eggie.io", null)).toBe("http://localhost:5173/kitchens/open?host=foo.eggie.io"));
it.each(["http://app.eggie.io", "javascript:alert(1)", "", "not a url"])("refuses web app %s", (webApp) =>
  expect(openKitchenUrl(webApp, "foo.eggie.io", null)).toBeNull());
it.each(["not_signed_in", "session_expired"] as const)("redirects at once for %s", (r) =>
  expect(siteSignedOutAction(r)).toBe("redirect"));
it("offers a button after a refused code, so a failing redeem cannot loop", () =>
  expect(siteSignedOutAction("handoff_spent")).toBe("button"));
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run apps/console/src/site/signIn.test.ts` (in `runtime/web`)
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `signIn.ts`, boot/context plumbing and the screens**

`SignedOut` on a site with a usable link: `"redirect"` → `location.replace(link)` in an effect and the screen reads "Taking you to the Eggie web app to sign in…" with no Try again; `"button"` → primary **Sign in** anchor to the link (same tab), Try again kept as secondary. Without a link (no `web_app` yet), today's copy. The local VM branch is untouched. The desktop home passed is `useDesktopHome()`.

- [ ] **Step 4: Verify**

Run: `npm test && npm run typecheck` (in `runtime/web`)
Expected: pass. Then `npm run dev`, `?scenario=site-signed-out`: the page navigates to the mock web app link.

- [ ] **Step 5: Commit**

```bash
git add runtime/web docs/site-mode.md
git commit -m "console: a kitchen with no session signs in through the web app"
```

---

### Task 4: Console — the instance switcher

**Files:**
- Create: `runtime/web/apps/console/src/instances/instances.ts`
- Create: `runtime/web/apps/console/src/instances/instances.test.ts`
- Create: `runtime/web/apps/console/src/shell/InstanceSwitcher.tsx` (styles in `Shell.module.css`)
- Modify: `runtime/web/apps/console/src/shell/Shell.tsx` (signed-in brand label → `<InstanceSwitcher />`)
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (`/api/instances`; scenarios `kitchens` with one row per state, `no-kitchens`, `kitchens-down`)
- Modify: `docs/security.md`, `docs/architecture.md`, `docs/site-mode.md` (switcher leaves "Open points"; `GET /v1/instances` with the device token in the contract), `runtime/web/CLAUDE.md`

**Interfaces:**
- Consumes: `GET /api/instances` (Task 2); `openKitchenUrl`, `useWebApp` (Task 3); `useSite`, `useDesktopHome`.
- Produces: `type Kitchen = {id, display_name, console_hostname, status, signed_out, is_owner, current}`; `type SwitcherEntry = {key: string, label: string, note: string | null, current: boolean, target: {kind: "kitchen", host: string} | {kind: "local"} | null}`; `switcherEntries({kitchens, site, desktopHome}: {kitchens: Kitchen[]; site: string | null | undefined; desktopHome: string | null}): SwitcherEntry[]`.

- [ ] **Step 1: Write the failing tests** (`instances.test.ts`)

```ts
const k = (over = {}) => ({ id: "i1", display_name: "Foo", console_hostname: "foo.eggie.io", status: "running", signed_out: false, is_owner: true, current: false, ...over });
const home = "http://127.0.0.1:5123";

it("local VM in the desktop: This computer current, then kitchens", () =>
  expect(switcherEntries({ kitchens: [k()], site: null, desktopHome: home }).map((e) => [e.label, e.current, e.target?.kind ?? null]))
    .toEqual([["This computer", true, null], ["Foo", false, "kitchen"]]));
it("local VM in a browser: This computer still labels where you are, not clickable", () =>
  expect(switcherEntries({ kitchens: [k()], site: null, desktopHome: null })[0]).toMatchObject({ label: "This computer", current: true, target: null }));
it("kitchen in the desktop: This computer leads home, this kitchen is current", () =>
  expect(switcherEntries({ kitchens: [k({ current: true })], site: "foo.eggie.io", desktopHome: home }).map((e) => [e.label, e.current, e.target?.kind ?? null]))
    .toEqual([["This computer", false, "local"], ["Foo", true, null]]));
it("kitchen in a browser: no This computer", () =>
  expect(switcherEntries({ kitchens: [k({ current: true })], site: "foo.eggie.io", desktopHome: null }).map((e) => e.label)).toEqual(["Foo"]));
it.each([
  [{ is_owner: false }, "Owned by a teammate"],
  [{ status: "provisioning" }, "Starting…"],
  [{ status: "booting" }, "Starting…"],
  [{ status: "failed" }, "Failed, see the web app"],
  [{ signed_out: true }, "Set up again in the web app"],
])("a kitchen %o is shown with %s and cannot be opened", (over, note) =>
  expect(switcherEntries({ kitchens: [k(over)], site: null, desktopHome: null })[1]).toMatchObject({ note, target: null }));
it("a running kitchen you own opens by its host", () =>
  expect(switcherEntries({ kitchens: [k()], site: null, desktopHome: null })[1].target).toEqual({ kind: "kitchen", host: "foo.eggie.io" }));
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run apps/console/src/instances/instances.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `instances.ts`** (owner check before status, as on the web app page) **and `InstanceSwitcher.tsx`**

Popover pattern copied from `AccountMenu` (outside pointerdown and Escape close). Button label: "This computer" when `site` is null; else the current kitchen's `display_name`, falling back to `site` until the list loads. Query `["instances"]`, `enabled: open`, `staleTime: 0`. Click on `kitchen` target → `location.assign(openKitchenUrl(webApp, host, desktopHome))`; when that returns null (no usable `web_app`), kitchen rows render disabled; on `local` → `location.assign(desktopHome + "#console")`. Empty list → "Kitchens are created in the Eggie web app" linking (same tab) to `<web_app>/kitchens`. Load error → inline message + Retry; `409 not_signed_in` is left to `AccountMenu`'s existing sign-out handling.

- [ ] **Step 4: Verify**

Run: `npm test && npm run typecheck && npm run build && npm run check-offline` (in `runtime/web`)
Expected: pass. `npm run dev` with `?scenario=kitchens`, `no-kitchens`, `kitchens-down`, `site`: each renders per the spec's tables.

- [ ] **Step 5: Docs** — `security.md`: the desktop window now shows web app and kitchen pages; the bridge guard refuses them as it does the VM console, its residual race applies to them too; kitchen codes still need a web session. `architecture.md`: the sign-in round trip and the switcher in one short paragraph. `site-mode.md` per Files.

- [ ] **Step 6: Commit**

```bash
git add runtime/web docs
git commit -m "console: switch between this computer and the account's kitchens"
```

---

### Task 5: Desktop — `#console` route

**Files:**
- Modify: `host/desktop/ui/app.js` (`ROUTES`)
- Modify: `host/desktop/CLAUDE.md` (one line: `#console` is entered by the console's "This computer")

**Interfaces:**
- Consumes: existing `api().enter_console()` → `{ok, url}` or `{ok: false, message}`.
- Produces: `<local UI>#console` lands in the local console when the VM is running.

- [ ] **Step 1: Add the route**

`'console': async () => { const r = await api().enter_console(); if (r.ok) return window.location.assign(r.url); await refresh(); showNotice(r.message); }` — not through `fromTray` (nothing to stash: the page just loaded). `start()` already clears the fragment before dispatch.

- [ ] **Step 2: Verify**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/desktop -q`
Expected: pass (`test_ui_assets.py` still finds every template and the CSP).
Manual (on Windows, by the user): from a kitchen in the desktop window, "This computer" → local console directly; with the VM stopped → Home with the notice.

- [ ] **Step 3: Commit**

```bash
git add host/desktop
git commit -m "desktop: #console route enters the local console"
```

---

### Task 6: PRs and review

- [ ] **Step 1:** Push `eggie-web` branch, open its PR to `main` (Task 1). Body: the URL contract, the no-backend-change note, what was left untested (the page component; its decision is tested).
- [ ] **Step 2:** Push `feature/48-instance-switcher`, open the PR to `main` with `gh pr create --head feature/48-instance-switcher` (origin needs `--head`). Body links the `eggie-web` PR and says it must deploy first; live check on a real kitchen: direct visit → web app → back signed in; switch local → kitchen → kitchen → This computer in the desktop app.
- [ ] **Step 3:** Run a code review of each PR with a separate agent and address its findings.
