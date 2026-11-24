# Desktop events

The desktop app sends a few anonymous events to the Eggie service so we can see where installs and
first sign-ins fail. They go to `POST https://app.eggie.io/api/v1/desktop-events`, one per request,
and only from the installed app. A source run sends nothing unless `EGGIE_EVENTS_URL` is set.

## What is sent

- An event name and a few properties (below).
- A random `session_id`, new on every launch and kept only in memory.
- The app version, OS (`windows` or `macos`), OS version and CPU architecture.

## What is never sent

- No name, email, account, IP address, cookie or machine id. The service does not store the IP.
- No file paths or user names: error messages are cleaned first (home folder, user name, emails and
  other absolute paths are replaced) and cut to 200 characters.
- Nothing from your projects, and nothing is written to disk besides a flag that the first launch
  was reported.

## Events

| Event | Properties |
|---|---|
| `app_first_launch` | none |
| `app_launch` | `background`: launched hidden at login |
| `env_setup_started` | none |
| `env_setup_step_failed` | `step`, `error_code` |
| `env_setup_succeeded` | `duration_sec` |
| `signup_screen_shown` | none (sent by the VM on behalf of the console) |
| `signup_started` | `method`: `new` or `existing` (same) |
| `app_error` | `error_code` prefixed with the job (`vm`, `recover`, `repair`, `runtime_update`, `uninstall`, `app_update`; `install` only for a failure outside any setup step), and a cleaned `message` |

`step` is one of `preflight`, `install_runtime` (macOS), `remediate`, `reboot_gate` (Windows),
`fetch_image` (Windows), `create_vm`, `ssh_alias` (macOS), `bootstrap`, `connect`, `verify`,
`finish`.

## Error codes

`error_code` is `<code>`, or `<code>:<hresult>` when a Windows/WSL HRESULT such as `0x8007274c` is
in the error. A failure not in the table sends the exception class name (`OSError`), meaning "not
catalogued yet".

| Code | Step(s) | What happened |
|---|---|---|
| `windows_build_too_old` | preflight | Windows is older than build 19045; the user must run Windows Update. |
| `cpu_virtualization_off` | preflight | CPU virtualization (VT-x / AMD-V) is turned off in the BIOS/UEFI. |
| `low_disk_space` | preflight | Less than 10 GB free disk space. |
| `macos_too_old` | preflight | macOS older than 13. |
| `elevation_declined` | remediate, create_vm | The user dismissed the Windows administrator prompt (turning on WSL features, or forwarding a port). |
| `wsl_features_failed` | remediate | `wsl --install --no-distribution` (turning on Virtual Machine Platform / WSL) failed. |
| `wsl_update_failed` | remediate | `wsl --update` (installing Store WSL) failed. |
| `download_failed` | fetch_image, install_runtime | The Ubuntu image or the Lima download could not be fetched (network). |
| `checksum_mismatch` | fetch_image, install_runtime | A download finished but its SHA-256 did not match. |
| `lima_unsupported_cpu` | install_runtime | No Lima build for this Mac's processor. |
| `lima_archive_invalid` | install_runtime | The Lima download is missing `limactl` or holds an unsafe path. |
| `lima_not_runnable` | install_runtime | The downloaded `limactl` failed to run. |
| `lima_wrong_version` | install_runtime | The downloaded `limactl` reports another version than the app expects. |
| `rootfs_missing` | create_vm | The downloaded Ubuntu image is not on disk when the VM is created. |
| `vm_import_failed` | create_vm | `wsl --import` refused to create the VM. |
| `vm_create_failed` | create_vm | `limactl start` could not create the VM. |
| `vm_configure_failed` | create_vm | Writing the VM's first boot settings (`/etc/wsl.conf`) failed. |
| `vm_start_failed` | create_vm, bootstrap | The VM exists but would not start (or restart). |
| `vm_stop_failed` | bootstrap | The VM would not stop for its first restart. |
| `vm_unresponsive` | any | The VM exists but does not answer (the WSL service hang, usually with `:0x8007274c`). |
| `port_forward_failed` | create_vm | A host port could not be forwarded into the VM (often another program holds it). |
| `ssh_setup_failed` | ssh_alias | Lima's SSH details or the VM's host keys could not be read, or `~/.ssh` could not be written. |
| `bootstrap_failed` | bootstrap | The Eggie installer inside the VM exited with an error (apt, Docker, image pull, skills). |
| `bootstrap_incomplete` | bootstrap | The installer reported success but did not finish (no runtime marker). |
| `bootstrap_bad_source` | bootstrap | The runtime URL or ref holds characters the VM command line cannot carry (dev overrides only). |
| `api_unreachable` | connect, verify | The Eggie service inside the VM did not answer in time. |
| `api_incompatible` | connect | The service inside the VM speaks an API version this app does not, and updating it did not help. |
| `api_not_accepted` | connect | The service inside the VM refused this computer's token, even after a reinstall. |
| `api_error` | connect, verify | The service inside the VM answered with an error not listed above. |
| `verify_no_answer` | verify | The test project never answered on its address. |
| `verify_http_error` | verify | The test project answered, but not with HTTP 200. |
| `verify_not_running` | verify | The test project's containers did not stay running, or did not start. |
| `verify_no_address` | verify | The test project started but exposed no HTTP address. |
| `verify_cleanup_failed` | verify | The test project worked but could not be removed afterwards. |
| `job_timeout` | verify | A job in the VM (starting the test project) took too long. |
| `job_failed` | verify | A job in the VM (starting the test project) failed. |
| `vm_remove_failed` | — (`uninstall/`) | The VM could not be deleted (`wsl --unregister`, `limactl delete`). |
| `wsl_restart_failed` | — (`recover/`) | `wsl --shutdown` failed while recovering a VM that stopped answering. |
| `vm_running` | — (`uninstall/`) | Uninstall was asked for while the VM was still running. |
| `webview_unavailable` | — (`app_error`) | The window could not open (on Windows usually the missing WebView2 runtime). |
| `uncaught` | — (`app_error`) | An exception nothing caught; `message` says which. |
