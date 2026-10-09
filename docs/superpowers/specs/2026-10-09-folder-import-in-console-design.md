# Folder import in the console

Issue #67. Supersedes the "Import folder stays in the desktop app for now" decision in
`2026-09-23-desktop-embedded-console-design.md`.

## Problem

A project starts from one of three sources: scratch, GitHub, or a folder on this computer.
The console offers the first two on the projects page. The third is a screen in the desktop
app's local UI, mentioned by the console only in a footnote, and unreachable from a browser.
Three sources, two places, one of them a different-looking page.

## Decision

Folder import becomes a third source on the projects page, next to "New project" and "From
GitHub". The console does the whole import itself: the browser supplies the files, the console
packs them and sends one archive to the API. The desktop import screen, its bridge methods and
the host-side folder walk are removed.

This follows the architecture rule that the host is a VM shell and everything else lives in the
runtime: a later change to how import works needs no host release.

## How it works

1. **Picking.** A hidden `<input type="file" webkitdirectory>` opened by a button, or a folder
   dropped onto the dialog (walked with `webkitGetAsEntry`). Both yield files with a path
   relative to the chosen folder; the folder's own name is the first segment.
2. **Planning** (`imports/plan.ts`, pure). Drops any file with `.git`, `node_modules`, `.venv` or
   `__pycache__` as a path component, and `.eggie/overlay.yml`; these are the same exclusions
   `host/client.py` applies for the CLI and install verification. Derives the project id with
   `slugify(folderName)`. Reports file count, byte total and what was skipped.
3. **Review.** The dialog shows the name it will use, the counts, and a note when something was
   skipped. If a project with that id is already in the list, it offers **Merge into it**
   (default) or **Replace it**, with the same deletion warning the desktop screen had.
4. **Room check.** `GET /api/disk`; the raw byte total must fit with the same reserve the upload
   queue uses, or the dialog refuses before packing.
5. **Packing** (`imports/tar.ts`, pure). A ustar stream written in the browser, long names in a
   PAX header, piped through the native `CompressionStream("gzip")` and collected into a Blob.
   Not streamed as a request body: plain-HTTP `fetch` cannot stream uploads in Chrome, and the
   console is served over HTTP/1.1 on localhost. Empty directories are not carried.
6. **Sending.** Replace mode: `DELETE /api/projects/{id}` first (the other order would wipe the
   import). Then `POST /api/projects` (a `project_exists` answer is fine in merge mode), then
   `POST /api/projects/{id}/files` with the gzip body over `XMLHttpRequest`, which is the only
   way to show upload progress. `project_busy` is retried for up to a minute, as the host client
   does. The API's own messages for `payload_too_large` and `disk_full` are shown as they are.
7. **Done.** Navigate to the project page.

One request, no resume: a dropped connection restarts the import, as with the desktop screen.

## UI

- Empty state: three cards, "Start from scratch", "From a folder", "From GitHub". The desktop
  footnote goes.
- Populated list header: "From a folder", "From GitHub", "+ New project".
- The Files page keeps refusing dropped folders, but its notice now points at "From a folder".

## Removed from the host

`DesktopApi.choose_folder`, `inspect_folder`, `start_import`, `IMPORT_MODES`; `view.inspect_folder`;
the `import` and `import:progress` templates, the four "Import folder" tiles and the import
section of `app.js`; the tests for them. `host/client.py`'s `upload_directory` and its exclusion
sets stay: the CLI's `up` and the install verification still use them.

## Testing

- `plan.test.ts`: exclusions, slug from the first path segment, counts, skipped report.
- `tar.test.ts`: the archive round-trips through an independent reader in the test (header
  fields, checksum, 512-byte padding, long names via PAX); gunzip with Node's zlib.
- `send.test.ts`: order of calls per mode, `project_exists` tolerated only in merge mode,
  `project_busy` retried, other errors surfaced.
- Host: the shell test's method list drops `start_import`; the desktop suite otherwise shrinks.

## Verified in a browser, not by tests

The folder picker and `CompressionStream` in WebView2 and WKWebView, and Chrome's "Upload N
files?" confirm.
