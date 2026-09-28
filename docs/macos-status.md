# macOS / Lima status

**Confirmed once. Most of it has still never been run on a real Mac.** Windows/WSL2 is the
primary platform. `host/providers/lima.py` and `host/providers/omelet.yaml` carry banners that
match this page; keep all three in sync, and remove a banner claim only when a recorded run
covers it.

The full evidence trail from 2026-09-15 (commands, output, timestamps) is in git history as
`docs/lima-verification-report.md`, last present at commit `7204c3d`.

## Confirmed

On 2026-09-15: macOS 26.6.2 arm64, Lima 2.2.0, Apple Silicon.

- **The package builds.** `packaging/macos/build.sh` exits 0, and `version` and `selfcheck` pass
  against the frozen binary inside `Omelet.app`.
- **Lima is downloaded on its own.** `lima_install.install()` fetched and checksum-verified
  `lima-2.2.0-Darwin-arm64.tar.gz` into `~/.local/share/omelet/lima/`. You don't need Homebrew.
- **Lima is found when the app is launched from Finder.** LaunchServices starts apps with
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, and `find_limactl` still finds it.
- **One VM ran end to end.** A VM created by `LimaProvider.create()` from an *older* revision of
  `omelet.yaml` booted under `vz` and completed the runtime install, and both declared ports
  (39080, 39099) answered. This was found and inspected afterwards, not run as a recorded
  session. That config predates the `x86_64` image and the `ssh.localPort` field.

## Not run yet

- A recorded `omelet setup` from `create_vm` through `verify`, with the wall-clock time.
- `forward()` for extra ports through Lima's ssh control master (`ssh -F ~/.lima/omelet-vm/ssh.config -O forward …`).
- `forwards()`, which always returns an empty list, so `omelet port list` shows nothing on macOS
  even while tunnels are open. Decide whether that's acceptable.
- `stop()` / `destroy()` and `packaging/macos/uninstall.sh`.
- Whether Lima honours `omelet.yaml`'s `ssh.localPort` (39022). The console's SSH guide depends
  on it.
- The `x86_64` image on an Intel Mac, and whether `rosetta.enabled: true` makes sense there.
- Images with no arm64 build: the user should see an explanation, not `exec format error`.

## The run to do next

1. On a clean Mac, run `omelet doctor` and paste its output.
2. Do a full install and **record how long it took**.
3. Bring up the five compose files in `tests/fixtures/compose/` and count how many work
   unchanged.
4. Bring up an amd64-only image and check what the user sees.
5. Forward an extra port end to end, then release it.
6. Uninstall, and check that `limactl list`, the app, the `omelet` symlink and
   `~/.local/share/omelet` are all gone.

Record the results here and in [release-testing.md](release-testing.md).
