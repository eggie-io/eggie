# Kitchen sign-in through the web app, and the instance switcher

> **Superseded in part (2026-10-18):** the desktop window no longer browses kitchens. A kitchen picked
> there opens in the system browser, "This computer" is only the local VM's label, and the `home`
> hand-off and the `#console` desktop route were removed. See `docs/site-mode.md` for what is current.

eggie-resources#48 ("Cloud functionality"). Site mode (`docs/site-mode.md`) put the runtime on a
DigitalOcean server the Eggie service provisions, a "kitchen", with its console at
`https://<name>.eggie.io`. Two gaps:

1. Opening a kitchen's console directly (a bookmark, a typed address, an expired session) ends on
   "We've lost track of you" with only "Try again", which can never succeed. The only way in is
   the web app's "open console" button.
2. There is no way to move between the local VM and kitchens, or between kitchens, from the console.

Both are solved by one flow: **a kitchen signs people in through the web app.**

## 1. Intent

A kitchen never authenticates a person itself. A console without a session sends the browser to
the web app; the web app signs the person in if needed, mints the single-use code it already mints
for "open console", and sends the browser back to the kitchen. The switcher in the console's top
bar is then just a list of kitchens whose entries go through the same web app page.

Switching to a kitchen shows the same console the web app opens (projects, agents, everything),
served from `<name>.eggie.io` instead of `localhost`. Nothing is merged: each console shows only
its own instance.

## 2. Goals, non-goals, anti-goals

Goals:
- A direct visit to a kitchen console with no session ends signed in (after a web app login if the
  browser has no web app session), never at a dead end.
- From any signed-in console (local VM or kitchen), list the account's kitchens and open one.
- Inside the desktop app, go back to the local VM from a kitchen.
- No Eggie service backend change. No desktop (host) release required.

Non-goals:
- Creating, renaming, retrying or deleting kitchens from the console. The web app does that.
- One console showing projects from several instances.
- Remembering the last instance across desktop app launches (the app still opens on the local VM).
- Switching to the local VM from a plain browser.

Anti-goals (a failure even if it works):
- Anything inside a VM or kitchen (the guest CLI, a coding agent, the device token in `state.db`)
  being able to mint a kitchen sign-in code. Minting stays behind a person's web app session, which
  is how the service already has it (`_require_session` in `open_console`).
- The web app page redirecting anywhere but the `console_hostname` of a kitchen from the user's own
  list. No redirect target is ever taken from the URL.
- A redirect loop between a kitchen and the web app.

## 3. What exists and is reused

Eggie service (`eggie-web/src/backend`, unchanged):
- `GET /v1/instances` → `InstanceOut[]` for the current organization. Accepts a device token or a
  web session. Fields used: `id`, `display_name`, `console_hostname`, `status`
  (`provisioning | booting | running | failed`), `signed_out`, `is_owner`.
- `POST /v1/instances/{id}/console` → `{"url": "https://<console_hostname>/#handoff=<code>"}`.
  Web session only (`403 session_required` otherwise), owner only (`403 instance_forbidden`),
  running and not signed out only (`409 instance_not_running`).
- `POST /v1/instances/handoff/redeem`, called by the kitchen (`services/site.py`), unchanged.

Web app frontend (`eggie-web/src/frontend`):
- `requireSession` (`shared/auth/require-session.ts`) sends a signed-out visitor to
  `/login?redirect=<path+search>` and `safeRedirect` brings them back to that same-origin path.
- Generated hooks for the instance endpoints (`shared/api/generated/endpoints/instances`).

Console (`runtime/web`):
- `#handoff=` boot path; `#home=` desktop home (`desktop/desktop.ts`, accepts only
  `http://127.0.0.1:<port>`); `useSite()` from `/api/health`'s `site`.

## 4. The web app page: `/kitchens/open`

New authenticated route in the `instances` module: `/kitchens/open?host=<console_hostname>[&home=<url>]`.
Static segment, so it must win over `/kitchens/$instanceId` (TanStack Router ranks static
segments first; the builder confirms).

1. `requireSession` as for every authenticated page (login and back if needed).
2. List instances; find the one whose `console_hostname` equals `host` (exact, case-insensitive).
3. Outcomes:
   - not found → error state "This kitchen isn't in your account", link to `/kitchens`;
   - `is_owner` false → "Only the kitchen's owner can open its console", link to the kitchen page;
   - not `running`, or `signed_out` → the kitchen's state in words, link to the kitchen page;
   - otherwise `POST /v1/instances/{id}/console`, and on success
     `window.location.replace(url + home)`; on an API error show its message with a retry.
4. `home` is appended to the returned URL's fragment as `&home=<encoded>` only when it is
   `http://127.0.0.1:<port>` with no userinfo (same rule as the console's `isDesktopHome`);
   otherwise dropped. `replace`, not `assign`, so Back from the kitchen does not land on this page
   and mint again.

The page shows a short "Opening <display_name>…" while it works.

## 5. Kitchen console: never a dead end

- `ApiConfig.web_url` (env `EGGIE_WEB_URL`, default `https://app.eggie.io`). `/api/health` carries
  `"web_app": <web_url>` in both modes; the console needs it before any session exists. Like
  `EGGIE_CLOUD_URL` today it is not passed through `stack.yml`; the default serves production.
- The console builds the open link itself: `openKitchenUrl(webApp, host, home)` →
  `<web_app>/kitchens/open?host=<host>[&home=<home>]`, refusing a `web_app` that is not `https:`
  (except `http://localhost…` for development).
- `SignedOut` on a site:
  - reason `not_signed_in` or `session_expired` → redirect to the open link for `site` at once
    (`location.replace`), showing the screen's "taking you to sign in" state meanwhile;
  - reason `handoff_spent` (a code was presented and refused) → no automatic redirect; the screen
    offers a **Sign in** button with the same link. This is what prevents a loop when redeeming
    keeps failing (service down, revoked kitchen).
- The local VM's `SignedOut` is unchanged (desktop handoff).
- The terminal "set it up again from the web app" screen (`needsAccount` on a site) gains a link
  to `<web_app>/kitchens`; nothing else changes there.

## 6. The switcher

### Runtime API

New feature area `instances`:
- `infra/cloud.py`: `list_instances(token)` → `GET /v1/instances`.
- `services/instances.py`: `Instances(account, cloud, site_host).list()` →
  `{"instances": [{"id", "display_name", "console_hostname", "status", "signed_out", "is_owner",
  "current"}]}`. Keeps the service's order; drops entries missing `id` or `console_hostname`;
  `current` = `console_hostname == site_host`. Errors: `NotSignedIn` → `Conflict("not_signed_in")`,
  `CloudUnavailable` → `Unavailable("cloud_unavailable")`, other `CloudError` →
  `Upstream("cloud_error", <service message>)`.
- `rest/routers/instances.py`: `GET /api/instances`, console-only (`build_console`, like
  `public.build_console`). The guest CLI has no use for it.
- No `API_VERSION` bump: additive, and the host never calls it.

### Who sees what

| Console | Entries |
|---|---|
| Local VM, desktop window | **This computer** (current), then every kitchen |
| Local VM, plain browser | **This computer** as the current label, not clickable, then every kitchen |
| Kitchen, desktop window | **This computer**, then every kitchen with this one marked current |
| Kitchen, plain browser | Every kitchen with this one marked current |

"Desktop window" = `useDesktopHome()` is not null.

### Entry states

| Kitchen | Shown as | Clickable |
|---|---|---|
| current | name, marked current | no |
| `running`, not signed out, owner | name | yes |
| not owner | name + "Owned by a teammate" | no |
| `provisioning` / `booting` | name + "Starting…" | no |
| `failed` | name + "Failed, see the web app" | no |
| `signed_out` | name + "Set up again in the web app" | no |

No kitchens: the current place plus "Kitchens are created in the Eggie web app" linking to
`<web_app>/kitchens`. A list that fails to load shows an inline error with retry; the switcher's
current label still renders.

### Switching

- **To a kitchen:** navigate the same tab/window to `openKitchenUrl(webApp, console_hostname, home)`
  (`window.location.assign`), not `openExternal`. In the desktop window the first switch shows the
  web app's login; the webview keeps that session afterwards.
- **To this computer (desktop only):** navigate to `<desktop home>#console` (§7).
- `home` survives kitchen → kitchen switches: each console stored the desktop home it was handed
  (`takeDesktopHome`) and passes it on through the web app page.

### UI

`shell/InstanceSwitcher.tsx` replaces the static "Kitchen" label in `Shell.tsx` when signed in,
using `AccountMenu`'s popover pattern (outside click and Escape close it). The button shows the
current place: "This computer" on the local VM; the kitchen's `display_name` on a kitchen (the
`site` host until the list loads). The list is a TanStack query `["instances"]` fetched when the
popover opens, not polled.

## 7. Desktop: `#console` route (optional host change)

`host/desktop/ui/app.js` already reads `#<route>` on load (`ROUTES`, used by the tray). Add
`console`: call the existing `enter_console()` bridge method and navigate to its URL; on failure
fall back to `refresh()` with the notice. An older desktop ignores the unknown route and shows
Home, one click from the local console, so the switcher works without a desktop release. The
bridge guard is unchanged: the route runs from the local UI page after it loaded.

## 8. Security

- Minting a kitchen code still requires a person's web app session; no device credential can do
  it. The device token is used only to list kitchens, which it can already do.
- `/kitchens/open` redirects only to a `console_hostname` from the signed-in user's own instance
  list, and only for kitchens they own. The `host` parameter selects, it is never the target.
  An attacker linking someone to it can at most sign that person into their own kitchen.
- `home` is filtered to `http://127.0.0.1:<port>` by the web app and again by the kitchen console.
- The desktop window now also shows the web app and kitchen consoles, pages from the internet. The
  bridge guard (`host/desktop/shell.py`) refuses every call unless the local UI is showing, as it
  does for the VM console; its documented residual race now applies to these pages too. New
  windows they open still go to the system browser, http(s) only. One line in `docs/security.md`.

## 9. Testing

Runtime (pytest, `fake_cloud.py`):
- `Instances.list` at the service seam: the real `InstanceOut` shape mapped, `current` only for
  the site host, empty list, entries without `console_hostname` dropped, not signed in, service down.
- `/api/instances` exists; `/instances` behind the bearer token does not.
- `/api/health` carries `web_app`.

Console (Vitest):
- `switcherEntries({instances, site, desktopHome})`: the four "who sees what" rows and each entry state.
- `openKitchenUrl`: with and without `home`, a refused non-https `web_app`, `host` encoded.
- The site `SignedOut` decision: redirect for `not_signed_in` / `session_expired`, button for
  `handoff_spent` (as a pure function, not a render test).

Web app (Vitest, `eggie-web/src/frontend`):
- The pure decision behind `/kitchens/open`: instance lookup by host (case), not found, not owner,
  not running, signed out, ready.
- `home` filter: loopback accepted, anything else dropped, fragment append correct.

Not tested: popover open/close, the `#console` route in `app.js` (glue over `enter_console`).

## 10. Docs

- `docs/site-mode.md`: "Account and sign-in" gets the web app round trip; "What the console shows"
  the redirect; the switcher leaves "Open points"; contract lists `GET /v1/instances` with a
  device token and the `/kitchens/open` page.
- `docs/security.md`: §8's lines.
- `docs/architecture.md`: the round trip and the switcher.
- `runtime/eggie_api/CLAUDE.md`, `runtime/web/CLAUDE.md`, `eggie-web/src/frontend/CLAUDE.md`
  (module table): one entry each.

## 11. Two repositories

- `eggie` (this repo): runtime API, console, optional desktop route, docs. Branch
  `feature/48-instance-switcher`.
- `eggie-web` (sibling checkout `../eggie-web`): the `/kitchens/open` page only. Its own branch and
  PR. Its working tree has unrelated local changes (`README.md`, an untracked key file) that must
  not be committed.

The kitchen redirect is useless until the web app page is deployed; land the web app PR first or
together.

## 12. Left to the builder

- Copy and layout of the switcher popover and the web app page; icons.
- Whether `/kitchens/open` renders inside the web app shell or a bare page.
- Whether the redirect screen on a kitchen shows a spinner, a line of text, or the egg.
