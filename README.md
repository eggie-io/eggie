# Eggie

<p align="center">
  <img src="https://github.com/eggie-io/.github/blob/main/profile/eggie-logo-lockup.png" width="196" alt="Eggie" />
</p>

[![Eggie Demo Video](https://img.youtube.com/vi/Y0OksMC7h2g/maxresdefault.jpg)](https://www.youtube.com/watch?v=Y0OksMC7h2g)

Eggie gives you a ready-to-use place on your own computer to build and run web apps with a coding
agent such as Claude Code, Codex or Cursor, without setting up a developer machine.

You install one app. It creates a private Linux machine on your computer and sets up
everything a project needs to run. You (or your coding agent) put a project there, and Eggie gives you
a link to open it in your browser.

> **Status:** early preview. Tested on Windows and on Macs with Apple Silicon. Intel Macs have not
> been tested.

## What you get

- **A safe sandbox.** Projects and coding agents run inside a separate Linux machine (a VM), not on
  your computer itself. Your drives and folders are not shared with it by default.
- **Any project runs.** Anything packaged with Docker Compose starts with one click, whatever
  language or framework it uses.
- **A link for every project**, such as `http://my-shop.local.eggie.space:39080`. No ports or
  config files to manage.
- **A console in your browser** to add, start, stop and watch projects, connect GitHub, fill in
  secrets (API keys, passwords) and see the logs when something fails.
- **Coding agents that know the setup.** Eggie connects Claude Code, Codex or Cursor to the VM and
  gives them instructions, so they build and run projects the Eggie way and hand you the link.
- **Optional sharing.** Sign in to an Eggie account to turn on a public URL for a project, or push
  it to a kitchen: an Eggie-hosted server that runs it online.

## Getting started

1. Download the installer from [Releases](../../releases): `EggieSetup-<version>.exe` for Windows,
   or the `.pkg` for your Mac (`arm64` for Apple Silicon, `x86_64` for Intel, untested).
2. Run it and open Eggie. The first start builds the VM and installs everything inside. It takes
   a few minutes, and on Windows it may ask you to restart once to turn on WSL.
3. When setup finishes, the Eggie window shows the console. Add a project there (upload a folder,
   clone from GitHub, or start a new one), or follow the guide to connect your coding agent and ask
   it to build something.

Eggie keeps itself up to date: the app offers an update when a new version is out, and the
inside of the VM updates on its own when it starts.

To remove everything, uninstall Eggie from **Apps & Features** on Windows, or see
[docs/vm.md](docs/vm.md#uninstalling) on macOS. This deletes the VM and every project in it.

## How it works

```
 Your computer
 ┌──────────────────────────────────────────────────────────────┐
 │  Eggie app ─── creates and starts ───┐                       │
 │  Browser   ─── localhost:39080 ──────┤                       │
 │  Coding agent ─ shell into the VM ───┤                       │
 │                                      ▼                       │
 │   ┌───────────── eggie-vm (Ubuntu + Docker) ──────────────┐  │
 │   │  router → console, Eggie API, your projects           │  │
 │   │  ~/projects/<name>/  each project runs in Docker      │  │
 │   └───────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────┘
```

- **The Eggie app** runs on your computer. Its only job is to create and run the VM (WSL2 on
  Windows, Lima on macOS) and show the console.
- **The runtime** is everything inside the VM: Docker, a small router, the Eggie API, the browser
  console, the `eggie` command your coding agent uses, and the agents' instructions.
- When a project starts, Eggie finds its web service in the compose file, routes a link to it and
  runs it with Docker. It never edits the project's files.

The app and the runtime are released separately, so most improvements arrive without reinstalling
the app.

## For developers

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.30" pyyaml httpx  # for API tests
.venv/bin/python -m pytest -q          # Python tests (host + API), no VM needed

cd runtime/web && npm install && npm run dev   # console on a mock API
```

On Windows, `powershell -ExecutionPolicy Bypass -File .\setup.ps1` does a full dev install and
creates the VM.

| Folder | What's in it |
|---|---|
| `host/` | The desktop app: creates the VM and shows the console |
| `runtime/` | Everything inside the VM: install scripts, API, console, `eggie` CLI, agent setup |
| `packaging/` | Windows installer, macOS package, container images |
| `tests/` | All Python tests |
| `docs/` | How-tos for development, building, releasing and the VM |

Start with [docs/README.md](docs/README.md) and [docs/architecture.md](docs/architecture.md).

## License

Source-available under the [Functional Source License 1.1 (FSL-1.1-ALv2)](LICENSE.md).
Each version becomes Apache 2.0 two years after its release.
