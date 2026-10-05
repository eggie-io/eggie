# Connect a Coding Agent Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A console picker and step-by-step guide for connecting Claude Code or Codex to the VM, driven by JSON files, with separate Windows (WSL) and Mac (Lima, SSH) guides.

**Architecture:** `install.sh` records the VM kind and login user in `/opt/eggie/connect.json`; `GET /connect` turns that into `{vm, ssh}`. The console fetches `/agents/index.json` + `/agents/<id>/agent.json` (static files copied into the `eggie-web` image), validates them in `agents/catalog.ts`, picks the platform from `vm` (browser fallback), and renders `/agents` and `/agents/:id`.

**Tech Stack:** FastAPI (API), bash (installer), React 19 + react-router 8 + TanStack Query 5 + Vitest (console), nginx.

**Spec:** `docs/superpowers/specs/2026-09-24-connect-agent-guide-design.md`

## Global Constraints

- Runtime only: no file under `host/` changes; no `API_VERSION` / `SUPPORTED_API` bump.
- SSH card values: Host `127.0.0.1` (the host's own `LOOPBACK`), Port `39022` (held equal to `host/providers/eggie.yaml` `ssh.localPort`), Key file `~/.lima/_config/user`. No command line, no password.
- `via_ssh: false` shows no card.
- Page must work offline; images and icons are served from `/agents/…` on the same origin.
- Open external links with `openExternal`, never `window.open` (none are needed here).
- Comments only for non-obvious reasons, no issue/doc references in code.

## Review Focus

- A guest with no `connect.json` (installed before this change): `/api/connect` must answer `{"vm":"other","ssh":null}`, and the console must still show guides chosen from the browser — covered in Task 1 and Task 3.
- A Lima guest where `login-users.sh` found nobody: `user` is `""`, `ssh` must be `null`, and the card must say the user is unknown instead of showing an empty copyable line — Task 1 and Task 3.
- A missing agent file served by nginx's SPA fallback as `index.html` with 200: must read as a malformed agent and be dropped, not crash the picker — Task 3 (`parseAgent` on non-object) and Task 4 (nginx `/agents/` returns real 404s).
- An agent id in the URL that is unknown or has no block for this platform: redirect to `/agents` — Task 5.
- `index.json` itself missing/malformed: `/agents` shows an error line, not a blank page — Task 3 (`parseIndex` throws) and Task 5.

---

### Task 1: `GET /connect` in the API

**Files:**
- Create: `runtime/eggie_api/core/connect.py`
- Modify: `runtime/eggie_api/core/constants.py` (add `CONNECT_FILE`, `LIMA_SSH_PORT`, `LIMA_KEY_FILE`)
- Modify: `runtime/eggie_api/core/config.py` (add `connect_path`)
- Modify: `runtime/eggie_api/routes/app.py` (route next to `/disk`)
- Test: `tests/runtime/api/test_connect.py`, `tests/test_constants_agree.py`

**Interfaces:**
- Produces: `connect.facts(path: Path) -> dict` returning `{"vm": "wsl"|"lima"|"other", "ssh": None | {"host": str, "port": int, "user": str, "key_file": str}}`; route `GET /connect` and `/api/connect` returning that dict.

- [ ] **Step 1: Write the failing tests** — `tests/runtime/api/test_connect.py`:

```python
import json

from eggie_api.core import connect


def _write(tmp_path, value):
    path = tmp_path / "connect.json"
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return path


def test_lima_with_a_user_gets_ssh_details(tmp_path):
    facts = connect.facts(_write(tmp_path, {"vm": "lima", "user": "ada"}))
    assert facts == {"vm": "lima", "ssh": {"host": "127.0.0.1", "port": 39022,
                                           "user": "ada", "key_file": "~/.lima/_config/user"}}


def test_lima_without_a_user_has_no_ssh_details(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "lima", "user": ""}))["ssh"] is None


def test_wsl_never_offers_ssh(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "wsl", "user": "ada"})) == {"vm": "wsl", "ssh": None}


def test_a_missing_file_reads_as_an_unknown_vm(tmp_path):
    assert connect.facts(tmp_path / "absent.json") == {"vm": "other", "ssh": None}


def test_a_corrupt_file_reads_as_an_unknown_vm(tmp_path):
    assert connect.facts(_write(tmp_path, "{not json")) == {"vm": "other", "ssh": None}


def test_an_unknown_vm_kind_is_not_passed_through(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "hyperv", "user": "ada"})) == {"vm": "other", "ssh": None}


def test_the_route_is_reachable_on_the_token_mount(env):
    env.config.connect_path.write_text(json.dumps({"vm": "wsl", "user": "ada"}))
    assert env.client.get("/connect").json() == {"vm": "wsl", "ssh": None}
```

Add `connect_path=tmp_path / "connect.json",` to the `ApiConfig(...)` in `tests/runtime/api/conftest.py`'s `env` fixture.

Append to `tests/test_constants_agree.py`:

```python
def test_the_ssh_port_the_console_shows_is_the_one_lima_is_asked_for():
    import yaml
    from pathlib import Path
    declared = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "host" / "providers" / "eggie.yaml").read_text())
    assert api_constants.LIMA_SSH_PORT == declared["ssh"]["localPort"]
```

- [ ] **Step 2: Run to verify failure**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_connect.py tests/test_constants_agree.py -q`
Expected: FAIL (`ImportError: cannot import name 'connect'`, missing constants).

- [ ] **Step 3: Implement**

`runtime/eggie_api/core/constants.py` — append:

```python
CONNECT_FILE = f"{GUEST_ROOT}/connect.json"
# What eggie.yaml asks Lima to forward; Lima has not been seen honouring it.
LIMA_SSH_PORT = 39022
LIMA_KEY_FILE = "~/.lima/_config/user"
```

`runtime/eggie_api/core/config.py` — field after `token_path`:

```python
    connect_path: Path = Path(constants.CONNECT_FILE)
```

`runtime/eggie_api/core/connect.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from . import constants

_KINDS = {"wsl", "lima"}


def facts(path: Path) -> dict:
    """How a coding agent on this computer reaches the VM, from what
    install.sh recorded. Anything unreadable reads as an unknown VM: the
    console then chooses a guide from the browser instead."""
    try:
        recorded = json.loads(path.read_text())
    except (OSError, ValueError):
        recorded = {}
    if not isinstance(recorded, dict):
        recorded = {}
    vm = recorded.get("vm") if recorded.get("vm") in _KINDS else "other"
    user = recorded.get("user") if isinstance(recorded.get("user"), str) else ""
    ssh = None
    if vm == "lima" and user:
        ssh = {"host": "127.0.0.1", "port": constants.LIMA_SSH_PORT,
               "user": user, "key_file": constants.LIMA_KEY_FILE}
    return {"vm": vm, "ssh": ssh}
```

`runtime/eggie_api/routes/app.py` — import `connect` alongside the other `core` imports and add after `/disk`:

```python
    @router.get("/connect")
    def connect_facts() -> dict:
        return connect.facts(Path(config.connect_path))
```

- [ ] **Step 4: Run to verify pass** — same command; then full `python3 -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "API: GET /connect reports how an agent reaches the VM"`

---

### Task 2: `install.sh` records `connect.json`

**Files:**
- Modify: `runtime/install/install.sh` (new step between the accounts loop and the marker)
- Test: `tests/runtime/test_install_shell.py`

**Interfaces:**
- Produces: `/opt/eggie/connect.json` = `{"vm": "wsl"|"lima"|"other", "user": "<name or empty>"}`, consumed by Task 1.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_install_records_how_agents_reach_the_vm_before_the_marker():
    text = INSTALL.read_text()
    record = text.index("/opt/eggie/connect.json")
    assert record < text.index('> /opt/eggie/runtime.version')
    assert "WSLInterop" in text and "/mnt/lima-cidata" in text
```

- [ ] **Step 2: Run** `python3 -m pytest tests/runtime/test_install_shell.py -q` — Expected: FAIL (`ValueError: substring not found`).

- [ ] **Step 3: Implement** — insert before `# 12. marker, last`:

```bash
# 12. what the console's "Connect an agent" guide needs to know.
if [[ -e /proc/sys/fs/binfmt_misc/WSLInterop ]]; then vm=wsl
elif [[ -d /mnt/lima-cidata ]]; then vm=lima
else vm=other
fi
agent_user=$(getent passwd | bash "$INSTALL_DIR/lib/login-users.sh" /etc/shells | head -n1 | cut -d: -f1)
printf '{"vm": "%s", "user": "%s"}\n' "$vm" "$agent_user" > /opt/eggie/connect.json
chmod 644 /opt/eggie/connect.json
```

Renumber the marker comment to `# 13.`. (`login-users.sh` only emits names from `/etc/passwd`, which cannot contain `"` or `\`, so printf-built JSON is safe.)

- [ ] **Step 4: Run** the test file plus `bash -n` test. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "install.sh: record the VM kind and login user for the agent guide"`

---

### Task 3: Console catalog and card logic

**Files:**
- Create: `runtime/web/apps/console/src/agents/catalog.ts`
- Create: `runtime/web/apps/console/src/agents/card.ts`
- Test: `runtime/web/apps/console/src/agents/catalog.test.ts`, `runtime/web/apps/console/src/agents/card.test.ts`

**Interfaces:**
- Produces:
  - `type Platform = "windows" | "mac"`
  - `interface Step { title: string; body: string; screenshot: string | null; alt: string }`
  - `interface Guide { viaSsh: boolean; tagline: string; steps: Step[] }`
  - `interface Agent { id: string; name: string; icon: string; platforms: Partial<Record<Platform, Guide>> }`
  - `interface Connect { vm: "wsl" | "lima" | "other"; ssh: { host: string; port: number; user: string; key_file: string } | null }`
  - `parseIndex(value: unknown): string[]` (throws `Error` on bad shape)
  - `parseAgent(id: string, value: unknown, base: string): Agent | null` (paths resolved to `${base}/${id}/${path}`)
  - `platformFor(connect: Connect | undefined, userAgent: string): Platform`
  - `loadCatalog(fetchJson: (url: string) => Promise<unknown>, base?: string): Promise<Agent[]>` (drops agents that fail to fetch or parse)
  - `cardRows(guide: Guide, connect: Connect | undefined): CardRow[] | null` with `interface CardRow { label: string; value: string; copy: boolean }`

- [ ] **Step 1: Write the failing tests**

`catalog.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { loadCatalog, parseAgent, parseIndex, platformFor } from "./catalog";

const guide = (over = {}) => ({
  via_ssh: true,
  tagline: "SSH connection",
  steps: [{ title: "Open it", body: "Sign in.", screenshot: "mac/1.png", alt: "Start screen" }],
  ...over,
});

describe("parseAgent", () => {
  it("resolves icon and screenshots against the agent's folder", () => {
    const agent = parseAgent("claude-code", { name: "Claude Code", icon: "icon.svg", platforms: { mac: guide() } }, "/agents");
    expect(agent?.icon).toBe("/agents/claude-code/icon.svg");
    expect(agent?.platforms.mac?.steps[0].screenshot).toBe("/agents/claude-code/mac/1.png");
    expect(agent?.platforms.mac?.viaSsh).toBe(true);
  });

  it("keeps a step without a screenshot as a placeholder", () => {
    const steps = [{ title: "Open it", body: "Sign in.", alt: "Start screen" }];
    const agent = parseAgent("codex", { name: "Codex", icon: "icon.svg", platforms: { windows: guide({ steps }) } }, "/agents");
    expect(agent?.platforms.windows?.steps[0].screenshot).toBeNull();
  });

  it("drops an agent that is not an object, such as the SPA's index.html", () => {
    expect(parseAgent("codex", "<!doctype html>", "/agents")).toBeNull();
  });

  it("drops an agent whose guide has no steps", () => {
    expect(parseAgent("codex", { name: "Codex", icon: "icon.svg", platforms: { mac: guide({ steps: [] }) } }, "/agents")).toBeNull();
  });

  it("ignores platforms it does not know", () => {
    const agent = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { linux: guide(), mac: guide() } }, "/agents");
    expect(Object.keys(agent?.platforms ?? {})).toEqual(["mac"]);
  });

  it("refuses a path that climbs out of the agent's folder", () => {
    expect(parseAgent("codex", { name: "Codex", icon: "../../x.svg", platforms: { mac: guide() } }, "/agents")).toBeNull();
  });
});

describe("parseIndex", () => {
  it("returns the ids in order", () => {
    expect(parseIndex({ agents: ["claude-code", "codex"] })).toEqual(["claude-code", "codex"]);
  });
  it("throws on anything else", () => {
    expect(() => parseIndex("<!doctype html>")).toThrow();
    expect(() => parseIndex({ agents: ["../x"] })).toThrow();
  });
});

describe("platformFor", () => {
  it("trusts the VM over the browser", () => {
    expect(platformFor({ vm: "wsl", ssh: null }, "Macintosh")).toBe("windows");
    expect(platformFor({ vm: "lima", ssh: null }, "Windows NT")).toBe("mac");
  });
  it("falls back to the browser when the VM is unknown", () => {
    expect(platformFor({ vm: "other", ssh: null }, "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)")).toBe("mac");
    expect(platformFor(undefined, "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
  });
});

describe("loadCatalog", () => {
  it("keeps the good agents when one fails to load", async () => {
    const files: Record<string, unknown> = {
      "/agents/index.json": { agents: ["claude-code", "codex"] },
      "/agents/claude-code/agent.json": { name: "Claude Code", icon: "icon.svg", platforms: { mac: guide() } },
    };
    const fetchJson = async (url: string) => {
      if (!(url in files)) throw new Error("404");
      return files[url];
    };
    expect((await loadCatalog(fetchJson)).map((a) => a.id)).toEqual(["claude-code"]);
  });
});
```

`card.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Guide } from "./catalog";
import { cardRows } from "./card";

const ssh: Guide = { viaSsh: true, tagline: "", steps: [] };
const wsl: Guide = { viaSsh: false, tagline: "", steps: [] };

describe("cardRows", () => {
  it("shows no card for a guide that does not go over SSH", () => {
    expect(cardRows(wsl, { vm: "lima", ssh: { host: "127.0.0.1", port: 39022, user: "ada", key_file: "~/k" } })).toBeNull();
  });

  it("gives Host, Port, User and Key file as separate copyable lines", () => {
    const rows = cardRows(ssh, { vm: "lima", ssh: { host: "127.0.0.1", port: 39022, user: "ada", key_file: "~/k" } });
    expect(rows).toEqual([
      { label: "Host", value: "127.0.0.1", copy: true },
      { label: "Port", value: "39022", copy: true },
      { label: "User", value: "ada", copy: true },
      { label: "Key file", value: "~/k", copy: true },
    ]);
  });

  it("says the user is unknown rather than offering an empty copy", () => {
    const rows = cardRows(ssh, { vm: "other", ssh: null });
    expect(rows?.find((row) => row.label === "User")).toEqual({ label: "User", value: "your Mac user name", copy: false });
    expect(rows?.find((row) => row.label === "Port")?.value).toBe("39022");
  });
});
```

- [ ] **Step 2: Run** `cd runtime/web && npx vitest run apps/console/src/agents` — Expected: FAIL (modules missing).

- [ ] **Step 3: Implement**

`catalog.ts`:

```ts
export type Platform = "windows" | "mac";
const PLATFORMS: Platform[] = ["windows", "mac"];

export interface Step { title: string; body: string; screenshot: string | null; alt: string }
export interface Guide { viaSsh: boolean; tagline: string; steps: Step[] }
export interface Agent { id: string; name: string; icon: string; platforms: Partial<Record<Platform, Guide>> }
export interface Connect {
  vm: "wsl" | "lima" | "other";
  ssh: { host: string; port: number; user: string; key_file: string } | null;
}

const ID = /^[a-z0-9][a-z0-9-]*$/;
// Relative, no "..": nginx serves the whole site from the same root.
const PATH = /^(?!.*\.\.)[A-Za-z0-9._/-]+$/;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";

export function parseIndex(value: unknown): string[] {
  const ids = isObject(value) ? value.agents : undefined;
  if (!Array.isArray(ids) || !ids.every((id) => typeof id === "string" && ID.test(id))) {
    throw new Error("the agent list is damaged");
  }
  return ids as string[];
}

function parseStep(value: unknown, folder: string): Step | null {
  if (!isObject(value) || !text(value.title) || !text(value.body) || !text(value.alt)) return null;
  const shot = value.screenshot;
  if (shot !== undefined && shot !== null && !(typeof shot === "string" && PATH.test(shot))) return null;
  return { title: value.title, body: value.body, alt: value.alt, screenshot: typeof shot === "string" ? `${folder}/${shot}` : null };
}

function parseGuide(value: unknown, folder: string): Guide | null {
  if (!isObject(value) || typeof value.via_ssh !== "boolean" || !text(value.tagline) || !Array.isArray(value.steps)) return null;
  const steps = value.steps.map((step) => parseStep(step, folder));
  if (steps.length === 0 || steps.some((step) => step === null)) return null;
  return { viaSsh: value.via_ssh, tagline: value.tagline, steps: steps as Step[] };
}

export function parseAgent(id: string, value: unknown, base: string): Agent | null {
  if (!ID.test(id) || !isObject(value) || !text(value.name) || !(typeof value.icon === "string" && PATH.test(value.icon))) return null;
  if (!isObject(value.platforms)) return null;
  const folder = `${base}/${id}`;
  const platforms: Agent["platforms"] = {};
  for (const platform of PLATFORMS) {
    if (!(platform in value.platforms)) continue;
    const guide = parseGuide(value.platforms[platform], folder);
    if (guide === null) return null;
    platforms[platform] = guide;
  }
  return { id, name: value.name, icon: `${folder}/${value.icon}`, platforms };
}

export function platformFor(connect: Connect | undefined, userAgent: string): Platform {
  if (connect?.vm === "wsl") return "windows";
  if (connect?.vm === "lima") return "mac";
  return /Mac/.test(userAgent) ? "mac" : "windows";
}

export async function loadCatalog(fetchJson: (url: string) => Promise<unknown>, base = "/agents"): Promise<Agent[]> {
  const ids = parseIndex(await fetchJson(`${base}/index.json`));
  const agents = await Promise.all(
    ids.map(async (id) => {
      try {
        return parseAgent(id, await fetchJson(`${base}/${id}/agent.json`), base);
      } catch {
        return null;
      }
    }),
  );
  return agents.filter((agent): agent is Agent => agent !== null);
}
```

`card.ts`:

```ts
import type { Connect, Guide } from "./catalog";

export interface CardRow { label: string; value: string; copy: boolean }

// Mirrors the API's fallbacks for a guest that could not say who its user is.
const PORT = "39022";
const KEY_FILE = "~/.lima/_config/user";

export function cardRows(guide: Guide, connect: Connect | undefined): CardRow[] | null {
  if (!guide.viaSsh) return null;
  const ssh = connect?.ssh;
  return [
    { label: "Host", value: ssh?.host ?? "127.0.0.1", copy: true },
    { label: "Port", value: ssh ? String(ssh.port) : PORT, copy: true },
    ssh ? { label: "User", value: ssh.user, copy: true } : { label: "User", value: "your Mac user name", copy: false },
    { label: "Key file", value: ssh?.key_file ?? KEY_FILE, copy: true },
  ];
}
```

- [ ] **Step 4: Run** the vitest command and `npm run typecheck`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Console: agent catalog and connection card logic"`

---

### Task 4: Agent content, icons, and shipping it

**Files:**
- Create: `runtime/web/apps/console/agents/index.json`
- Create: `runtime/web/apps/console/agents/claude-code/agent.json`, `…/claude-code/icon.svg`
- Create: `runtime/web/apps/console/agents/codex/agent.json`, `…/codex/icon.svg`
- Modify: `runtime/web/Dockerfile` (copy `agents/` into the nginx root)
- Modify: `runtime/web/nginx.conf` (`/agents/` returns real 404s)
- Test: `runtime/web/apps/console/src/agents/content.test.ts`

**Interfaces:**
- Consumes: `parseIndex`, `parseAgent` from Task 3.
- Produces: files served at `/agents/…` in dev (Vite serves the app root) and in the image.

- [ ] **Step 1: Write the failing test** — every shipped file must parse, and every referenced screenshot/icon must exist:

```ts
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseAgent, parseIndex } from "./catalog";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../agents");
const read = (path: string) => JSON.parse(readFileSync(join(ROOT, path), "utf8"));

describe("shipped agent content", () => {
  const ids = parseIndex(read("index.json"));

  it.each(ids)("%s parses, has both platforms, and every file it names exists", (id) => {
    const agent = parseAgent(id, read(`${id}/agent.json`), "");
    expect(agent).not.toBeNull();
    expect(Object.keys(agent!.platforms).sort()).toEqual(["mac", "windows"]);
    expect(agent!.platforms.mac!.viaSsh).toBe(true);
    expect(agent!.platforms.windows!.viaSsh).toBe(false);
    const files = [agent!.icon, ...Object.values(agent!.platforms).flatMap((g) => g!.steps.map((s) => s.screenshot))];
    for (const file of files) if (file) expect(existsSync(join(ROOT, file)), file).toBe(true);
  });
});
```

- [ ] **Step 2: Run** `npx vitest run apps/console/src/agents/content.test.ts` — Expected: FAIL (ENOENT).

- [ ] **Step 3: Create content.** `index.json`: `{"agents": ["claude-code", "codex"]}`.

`claude-code/agent.json`:

```json
{
  "name": "Claude Code",
  "icon": "icon.svg",
  "platforms": {
    "mac": {
      "via_ssh": true,
      "tagline": "SSH connection",
      "steps": [
        {"title": "Open Claude Code", "body": "Open the Claude Code app and sign in.", "alt": "Screenshot · Claude Code start screen"},
        {"title": "Add an SSH connection", "body": "Open the environment menu next to the prompt box and pick “Add SSH connection”.", "alt": "Screenshot · environment menu with Add SSH connection"},
        {"title": "Enter the details", "body": "Host, port, user and key file. All four are in the card below — copy them one by one.", "alt": "Screenshot · SSH connection form, filled in"},
        {"title": "Start prompting", "body": "Pick the projects folder and tell it what to build.", "alt": "Screenshot · Claude Code prompt, connected to the kitchen"}
      ]
    },
    "windows": {
      "via_ssh": false,
      "tagline": "Inside WSL",
      "steps": [
        {"title": "Open Claude Code", "body": "Open the Claude Code app and sign in.", "alt": "Screenshot · Claude Code start screen"},
        {"title": "Switch to WSL", "body": "Open the environment menu next to the prompt box and pick WSL.", "alt": "Screenshot · environment menu with WSL"},
        {"title": "Pick eggie-vm", "body": "Choose the eggie-vm machine from the list.", "alt": "Screenshot · WSL machine list with eggie-vm"},
        {"title": "Start prompting", "body": "Pick the projects folder and tell it what to build.", "alt": "Screenshot · Claude Code prompt, connected to the kitchen"}
      ]
    }
  }
}
```

`codex/agent.json`:

```json
{
  "name": "Codex",
  "icon": "icon.svg",
  "platforms": {
    "mac": {
      "via_ssh": true,
      "tagline": "SSH connection",
      "steps": [
        {"title": "Open Codex", "body": "Open the Codex app and sign in.", "alt": "Screenshot · Codex start screen"},
        {"title": "Add a remote connection", "body": "Open the environment picker and choose to connect over SSH.", "alt": "Screenshot · Codex environment picker with SSH"},
        {"title": "Enter the details", "body": "Host, port, user and key file. All four are in the card below — copy them one by one.", "alt": "Screenshot · Codex SSH form, filled in"},
        {"title": "Start prompting", "body": "Open the projects folder and say what you want. It cooks inside the kitchen.", "alt": "Screenshot · Codex chat, connected to the kitchen"}
      ]
    },
    "windows": {
      "via_ssh": false,
      "tagline": "Inside WSL",
      "steps": [
        {"title": "Open Codex", "body": "Open the Codex app and sign in.", "alt": "Screenshot · Codex start screen"},
        {"title": "Run it in WSL", "body": "In Codex's settings, choose to run the agent in WSL.", "alt": "Screenshot · Codex settings with WSL"},
        {"title": "Pick eggie-vm", "body": "Choose the eggie-vm machine.", "alt": "Screenshot · WSL machine list with eggie-vm"},
        {"title": "Start prompting", "body": "Open the projects folder and say what you want. It cooks inside the kitchen.", "alt": "Screenshot · Codex chat, connected to the kitchen"}
      ]
    }
  }
}
```

Icons: 40×40 SVG monograms (rounded square, `CC` / `Cx`) with fixed colours (no CSS variables — loaded via `<img>`), replaceable by real logos later.

`Dockerfile` final stage, after the `dist` copy:

```dockerfile
COPY --from=build /runtime/web/apps/console/agents /usr/share/nginx/html/agents
```

`nginx.conf`, next to `/assets/`:

```nginx
    # No SPA fallback: a missing agent file must be a 404, not index.html.
    location /agents/ {
        include /etc/nginx/security-headers.inc;
        add_header Cache-Control "no-cache" always;
        try_files $uri =404;
    }
```

- [ ] **Step 4: Run** the content test. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Console: Claude Code and Codex guides as JSON content"`

---

### Task 5: Screens, routes, top-bar entry, mock API

**Files:**
- Create: `runtime/web/apps/console/src/agents/queries.ts`
- Create: `runtime/web/apps/console/src/components/CopyButton.tsx` (moved out of `AddressRows.tsx`)
- Create: `runtime/web/apps/console/src/screens/agents/AgentPicker.tsx`, `AgentGuide.tsx`, `AgentMenu.tsx`, `Agents.module.css`
- Modify: `runtime/web/apps/console/src/screens/project/AddressRows.tsx` (import the moved `CopyButton`)
- Modify: `runtime/web/apps/console/src/App.tsx` (routes `/agents`, `/agents/:id`)
- Modify: `runtime/web/apps/console/src/shell/Shell.tsx` + `Shell.module.css` (a "Connect an agent" link when signed in)
- Modify: `runtime/web/apps/console/src/screens/icons.tsx` (`PLUG`, `CHEVRON`, `CHECK`)
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (`/api/connect`, scenario `windows`)

**Interfaces:**
- Consumes: Task 3 (`loadCatalog`, `platformFor`, `cardRows`, types), Task 1 route shape.
- Produces: `useConnect()`, `useAgents()` → `{ platform, agents }` (agents already filtered to the platform).

- [ ] **Step 1: Queries** — `agents/queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { loadCatalog, platformFor, type Connect } from "./catalog";

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

export function useConnect() {
  return useQuery({ queryKey: ["connect"], queryFn: () => api.get<Connect>("/api/connect"), staleTime: Infinity });
}

export function useAgents() {
  const connect = useConnect();
  const catalog = useQuery({ queryKey: ["agents"], queryFn: () => loadCatalog(fetchJson), staleTime: Infinity });
  // An older API without /connect still gets guides, chosen by the browser.
  const settled = !connect.isPending;
  const platform = platformFor(connect.data, navigator.userAgent);
  const agents = catalog.data?.filter((agent) => agent.platforms[platform]);
  return { platform, agents: settled ? agents : undefined, connect: connect.data, error: catalog.error };
}
```

- [ ] **Step 2: Move `CopyButton`** verbatim from `AddressRows.tsx` into `components/CopyButton.tsx` (exported), import it back in `AddressRows.tsx`.

- [ ] **Step 3: Picker** — `AgentPicker.tsx`: heading "Which agent do you use?", subline "Pick one and we'll walk you through it. Takes about two minutes.", grid of `<Link to={`/agents/${id}`}>` cards (icon `<img alt="">` 40×40, name, the platform guide's tagline, chevron). States: `error` → `<p className={s.error}>Couldn't load the agent guides.</p>`; `agents === undefined` → "Getting the guides…"; empty list → "No guides for this computer yet.". Footer hint: "Not listed? Anything that connects over SSH works." only when the platform guide set has any `viaSsh` guide.

- [ ] **Step 4: Guide** — `AgentGuide.tsx`: read `:id`; once `agents` is loaded, `Navigate to="/agents" replace` if not found. Layout per design 4c/4d: bar row with title "Connect a coding agent" and `AgentMenu` (dropdown: each agent with icon, current one ticked; "See all agents"); two-column grid `318px minmax(0,1fr)`, collapsing to one column under 760px. Left: step buttons (number dot, title, body shown only on the active step), then the card from `cardRows(guide, connect)` titled "Your kitchen's key" with one row per `CardRow` (`CopyButton` when `copy`) and the note "Port 39022 is the one this machine asks for. If the connection is refused, ask us for help." Right: screenshot frame — `<img src alt>` when `screenshot`, otherwise a dashed placeholder showing `alt`; then "Step n of N", Back (disabled on step 1), Next / "Done" (Done navigates to `/`). Step index resets to 0 when `:id` changes (`key={id}` on the inner component).

`AgentMenu.tsx`: a button toggling a popover list; closes on selection, on Escape and on outside `pointerdown`.

- [ ] **Step 5: Routes & entry** — `App.tsx`: add `<Route path="/agents" element={<AgentPicker />} />` and `<Route path="/agents/:id" element={<AgentGuide />} />`. `Shell.tsx`: add a `leading?: ReactNode` prop rendered first inside `.end`; `App.tsx` passes `<Link className=… to="/agents">{PLUG}Connect an agent</Link>` for the signed-in shell, so navigation stays client-side and `Shell` needs no router. Style `.connect` like design 4a's yolk-soft button.

- [ ] **Step 6: Mocks** — `handlers.ts`: add `"windows"` to `SCENARIOS` and

```ts
    http.get("/api/connect", () => {
      const denied = guard();
      if (denied) return denied;
      if (scenario === "windows") return HttpResponse.json({ vm: "wsl", ssh: null });
      return HttpResponse.json({ vm: "lima", ssh: { host: "127.0.0.1", port: 39022, user: "ada", key_file: "~/.lima/_config/user" } });
    }),
```

- [ ] **Step 7: Verify** — `npm test`, `npm run typecheck`, `npm run build && npm run check-offline`; `npm run dev` and walk `/agents` → Claude Code → steps 1–4 → Done, then `?scenario=windows` (no card, WSL steps). Check that `/agents/index.json` is served raw by Vite in dev.

- [ ] **Step 8: Commit** — `git commit -m "Console: Connect an agent picker and guide"`

---

### Task 6: Docs

**Files:**
- Modify: `CLAUDE.md` (architecture bullets: `runtime/web` gains `agents/` content and `src/agents/`; `/connect` route; `connect.json` in the `install.sh` bullet; dev scenario list gains `windows`)

- [ ] **Step 1:** Edit the three bullets; add a "Things that will bite you" entry: `apps/console/agents/` is outside Vite's build output — the Dockerfile copies it, so `npm run build` alone serves no guides.
- [ ] **Step 2: Commit** — `git commit -m "CLAUDE.md: the agent guide's content and /connect"`
