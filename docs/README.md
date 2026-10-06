# Docs

| Doc | For |
|---|---|
| [development.md](development.md) | Setting up a checkout, running tests, the web UI dev server, debugging the API in a VM |
| [building.md](building.md) | Building the Windows installer, the macOS package, and the container images |
| [releasing.md](releasing.md) | Cutting runtime and desktop app releases, and how installed machines update |
| [vm.md](vm.md) | Running the VM by hand, looking inside it, troubleshooting, uninstalling |
| [security.md](security.md) | The VM trust boundary: what's inside it, what each secret does and doesn't protect, public URLs |
| [release-testing.md](release-testing.md) | The manual checks to run on real machines before shipping an installer |
| [macos-status.md](macos-status.md) | What has and hasn't been run on a real Mac |
| [design/](design/README.md) | Snapshot of the web UI design board |
| [superpowers/](superpowers/) | The original blueprint and every dated design spec and plan |

Code-level guidance for contributors and coding agents is in the `CLAUDE.md` files: one at the
repo root and one in each of `host/`, `runtime/`, `runtime/web/` and `runtime/eggie_api/`.
