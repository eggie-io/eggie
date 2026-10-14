# The VM

The guest is always Ubuntu 24.04 running Docker. It's named `eggie-vm` on both platforms.

| | Windows | macOS |
|---|---|---|
| Backend | WSL2 distro | Lima (`vz`) |
| Host-side data | `%LOCALAPPDATA%\Eggie\` (`vm\`, `cache\`) | `~/.local/share/eggie/` (`vm/`, `cache/`, `lima/`) and `~/.lima/eggie-vm/` |
| Shell as root | `wsl -d eggie-vm -u root --cd ~` | `limactl shell eggie-vm -- sudo -i` |

On Windows, always pass `-d eggie-vm`. Every WSL distro reports the Windows machine name as its
hostname, so the prompt doesn't tell you which one you're in. On macOS, `sudo` in the VM needs no
password, and `limactl list` shows the VM's status and ports. **Nothing from the Mac is mounted**
(`mounts: []`): projects reach the VM over HTTP uploads, not a shared folder.

An interactive login shell that starts in `$HOME` moves to `~/projects` (`/etc/profile.d/eggie-cwd.sh`,
installed by the runtime). On Windows add `--cd ~` — without it `wsl` opens in the current Windows folder
and stays there.

## Creating it by hand

`eggie setup` is the normal path. To use the lower-level commands on Windows, first point
`EGGIE_ROOTFS` at Canonical's Ubuntu 24.04 WSL image, using a **Windows** path because it goes
straight to `wsl --import`:

- amd64: <https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-wsl-amd64.wsl>
  (sha256 `9b2f7730dc68227dd04a9f3e5eab86ad85caf556b8606ad94f1f29ff5c4fd3f5`)
- arm64: <https://cdimages.ubuntu.com/releases/24.04.4/release/ubuntu-24.04.4-wsl-arm64.wsl>

```powershell
$env:EGGIE_ROOTFS = "C:\Users\you\Downloads\ubuntu-24.04.4-wsl-amd64.wsl"
eggie vm create      # import, enable systemd, install the runtime
eggie vm start | vm stop | vm destroy
eggie port add <guest> <host> | port remove <guest> <host> | port list
```

`vm create` rewrites `/etc/wsl.conf` to `[boot] systemd=true`, so the imported distro starts as root.
The runtime install then adds the `eggie` account (passwordless `sudo`) and makes it the
default user from the VM's next start; until then `wsl -d eggie-vm` still opens as root.

## Where things are inside

| Path | What |
|---|---|
| `/opt/eggie/runtime.version` | Installed runtime tag. If this file is missing, the runtime isn't installed |
| `/opt/eggie/runtime/` | The unpacked `runtime/` tree of that tag |
| `/opt/eggie/stack.yml` | Traefik + API + web compose stack |
| `/opt/eggie/api.token` | Shared secret between the host and the API |
| `/opt/eggie/state.db` | Project state (sqlite) |
| `/opt/eggie/projects/<id>/` | Each project; the generated Traefik overlay is at `.eggie/overlay.yml` |
| `/opt/eggie/uploads/` | Partially uploaded files |
| `/opt/eggie/tunnel/token` | Public-URL tunnel token; present only while a public URL is on |
| `/opt/eggie/connect.json` | VM kind and login user, shown by the console's agent guide |

```bash
docker ps                                                   # traefik, api, web, projects
docker compose -f /opt/eggie/stack.yml logs -f api
curl -s 127.0.0.1:39099/health
bash /opt/eggie/runtime/install/install.sh "$(cat /opt/eggie/runtime.version)" --repair  # re-run the install, output shown live
```

Ports: the API listens on `39099`, and all project and console traffic enters through Traefik on
`39080`. Project URLs look like `http://<name>.local.eggie.space:39080`.

## Troubleshooting

- **WSL says the VM is running but nothing answers.** WSL's service can hang, often after sleep,
  with `Wsl/Service/CreateInstance/0x8007274c`, while `wsl -l --running` still lists `eggie-vm`.
  Try `wsl --terminate eggie-vm` first. `wsl --shutdown` always clears it, but it also stops
  every other distro and Docker Desktop.
- **The install failed partway.** `runtime.version` is written last, so a failed install never
  looks installed. Re-run setup, or run `install.sh ... --repair` in the VM (above) to see the
  output live.
- **`eggie doctor`** lists what the host is missing and exits non-zero if the host is
  unsupported.

## Uninstalling

```bash
eggie uninstall --purge                 # destroys the VM, every project in it, and the cache
bash packaging/macos/uninstall.sh        # macOS, installed from the .pkg: also removes the app (run as yourself, not sudo)
```

On Windows, the uninstaller in Apps & Features runs `eggie uninstall --purge` itself.
