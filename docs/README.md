# Docs

| Doc | For |
|---|---|
| [architecture.md](architecture.md) | The high-level map: host vs runtime, the contract between them, the in-VM stack, updates, platforms |
| [development.md](development.md) | Setting up a checkout, running tests, the web UI dev server, debugging the API in a VM |
| [building.md](building.md) | Building the Windows installer, the macOS package, and the container images |
| [releasing.md](releasing.md) | Cutting runtime and desktop app releases, and how installed machines update |
| [vm.md](vm.md) | Running the VM by hand, looking inside it, troubleshooting, uninstalling |
| [site-mode.md](site-mode.md) | The runtime on a server the Eggie service provisions: how it boots signed in, the edge and TLS, sign-in from the web app, what the console shows |
| [security.md](security.md) | The VM trust boundary: what's inside it, what each secret does and doesn't protect, public URLs |

Code-level guidance for contributors and coding agents is in the `CLAUDE.md` files: one at the
repo root and one in each of `host/`, `runtime/`, `runtime/web/` and `runtime/eggie_api/`.
