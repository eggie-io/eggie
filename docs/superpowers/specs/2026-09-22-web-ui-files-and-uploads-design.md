# Eggie web UI — part D: files and uploads

Date: 2026-09-22
Builds on: `2026-09-21-web-ui-agent-prerequisites-design.md` (§1 decisions, §5
upload protocol, §8 "D"), `2026-09-22-web-ui-shell-and-kit-design.md` (shell, kit,
client, mocks), `2026-09-22-web-ui-project-screens-design.md` (project page, tiles).
Board frames: 07, 08, 09, 10, 13, 14 (`docs/design/`).

## 1. Outcome

A signed-in user opens a project's files, walks its folders, downloads a file,
and sends files in — big ones included — to the folder they belong in. Uploads
survive moving around the app, pause and carry on, recover from a dropped
connection, a busy project and a full disk, and after a reload pick up where they
stopped once the user hands the same file back. A database dump or an archive
ends with a prompt for the coding agent.

Not in scope: deleting, renaming or moving files (removing things stays the coding
agent's job — "this page won't delete anything on its own"); uploading folders;
uploads that continue with the page closed (§1 scope cut); freeing space (desktop
work). No agent changes.

## 2. Routes and entry

- `/p/:id/files` and `/p/:id/files/*` — the splat is the folder path inside the
  project, so Back, reload and "Show me" all work. Each segment is URL-encoded.
- The project page's Files tile (`screens/project/Tiles.tsx`) stops being a
  disabled "Soon" button and links to `/p/:id/files`.

## 3. Screens

### Files (frame 07)

- Header: back link to the project (`‹ recipe-box`), title "Files", breadcrumb
  `recipe-box / data / …` with every segment a link, and an **Upload** button.
- Listing from `GET /api/projects/{id}/files?dir=<path>` (empty `dir` is the
  top): folders first, then files, as the agent orders them. Columns Name, Size
  (a file's size, a folder's "N items", or "—" when the agent reports
  `items: null`), Changed (relative time, reusing `projects/format.ts`).
- A folder row opens it. A file row is a download link to
  `/api/projects/{id}/files/{path}` (the agent answers with
  `Content-Disposition: attachment`).
- Footer line: "N things in here" and "Drag files in from your desktop, or use
  Upload to choose where they land."
- States: empty folder shows the drag line alone; `permission_denied` (409) →
  "Eggie can't look inside this folder — a program in the project owns it.";
  `folder_not_found` (404) → replace the URL with the project's top;
  `project_not_found` → back to the list, as the project page does.
- The "Carrying things in" panel (below) sits above the listing whenever the
  queue holds anything for this project.

### Getting files in

- **Upload** opens the file picker (multiple allowed), then the destination
  dialog.
- **Dropping files** on the listing queues them straight into the folder on
  screen — no dialog. A dropped folder (an entry with no file behind it) is
  refused with "Folders can't go up as they are — zip it first, then drop the
  zip." Other dropped files in the same drop still go.
- Before anything is queued, `GET /api/disk` is read. Any file with
  `size + 1 GiB > free_bytes` (the agent's own rule) is not queued; frame 13
  shows for it.
- `file_exists` from the agent asks "`<name>` is already in `data/`. Replace
  it?" — Replace re-queues with `replace: true`; Keep both is not offered;
  Skip drops it.

### Destination dialog (frame 08)

- Shows the picked file (name and size; with several, "3 files · 6.2 GB").
- Choices: the project's top ("recipe-box — the top of the project"), each of
  the top-level folders, and, when the user is deeper, the current folder too.
  The current folder is preselected.
- "New folder": a name field; the new folder goes inside the selected choice and
  is created by the agent when the upload finishes. A name that is empty or
  spaces only, contains `/` or `\`, or is `.`/`..` is refused inline.
- Path preview: `recipe-box/data/orders-dump.sql` (for several files, the folder
  only).
- Buttons "Send it up" and "Cancel"; note "Big files are fine — you can pause and
  come back."

### Won't fit (frame 13)

The destination dialog body is replaced for the file that won't fit: "This one
won't fit — the file is 6.4 GB and Eggie has 2.1 GB of room left. Make some
space in the desktop app, then come back and send it up." Buttons: "Pick a
smaller file" (reopens the picker) and "Cancel". The desktop line is text, not a
link. With several files, the ones that fit still go; the dialog lists the ones
that don't.

### Carrying things in (frames 09, 14)

Summary line "1 going up · 1 waiting · 1 stalled" and **Hide** (collapses to the
summary). One row per item, for this project only:

| State | Row | Actions |
|---|---|---|
| going | %, "2.8 GB of 4.4 GB · into data/", "about 4 minutes left" | Pause |
| going, last chunk busy | "Waiting for the project to finish starting" | — |
| waiting | "2.4 GB · waiting its turn" | Remove |
| paused | "Paused at 38%" | Carry on, Remove |
| stalled | "Pick up where it stopped — the connection dropped at 38%. Nothing was lost." (after a reload: "…the page was closed at 38%.") | Pick up where it stopped, Remove |
| noRoom | "Stopped at 71% · 3.1 GB of 4.4 GB got through" | Carry on |
| failed | plain reason (below) | Remove; Replace for `file_exists` |
| done | "In data/ · 84 MB" | Show me (opens that folder) |

`noRoom` also shows the frame-14 banner above the panel: "Eggie ran out of room
partway through — `<name>` got 71% of the way in. What made it is safe, and it
can carry on from there, but you'll need to free up space in the desktop app
first." with "I've freed some up — carry on".

Footer copy (replaces the board's): "Pausing is fine — nothing is lost. If you
close this page, uploads pick up where they stopped when you're back."

While an item is `going`, the page sets a `beforeunload` warning.

Failed reasons: `permission_denied` → "Eggie can't write into that folder — pick
another."; `project_not_found` → "The project is gone."; `path_is_folder` →
"There's a folder with that name already."; `upload_not_found` twice → "Eggie
lost this upload — send it again."; anything else → the agent's message.

### Done with a prompt (frame 10)

When an item finishes and `kinds.ts` recognises it, the Files screen shows the
card above the listing: "`orders-dump.sql` is in — it landed in `data/`. All
2.4 GB of it.", the kit's PromptCard with the prompt, "Back to files" (dismisses
it) and "Or ignore all this — the file is in the folder either way."

- **dump** — `.sql`, `.sql.gz`, `.dump`, `.bak`, `.backup` (case-insensitive):
  "I uploaded a database dump to `<path>` in this project. Please import it into
  the project's database using the credentials already configured, then tell me
  which tables landed and roughly how many rows each one has."
- **archive** — `.zip`, `.tar`, `.tar.gz`, `.tgz`: "I uploaded `<path>` to this
  project. Please unpack it where it belongs in the project, tell me what was
  inside, and delete the archive once everything is in place."
- Anything else: no card; the done row is the whole signal.

Only the most recently finished recognised file shows a card.

### After a reload

Opening Files for a project reads `GET /api/projects/{id}/uploads` and hands the
list to the queue. Each server upload the queue doesn't already hold becomes a
`stalled` row without a file. "Pick up where it stopped" opens the picker (single
file); a file whose `name:size:lastModified` differs from the stored
`fingerprint` is refused with "That's not the same file — pick
`<name>` (`<size>`)." A match resumes from the server's offset. Remove on such a
row cancels it on the agent.

## 4. The upload engine

All in `apps/console/src/uploads/`, no React in the engine.

### `uploadApi.ts`

Its own `createApi(fetch, { timeoutMs: 120_000 })` — the shared client stops at
10 s, too short for an 8 MiB chunk on a slow link. Calls: `start(projectId,
{path, size, fingerprint, replace})`, `patch(uploadId, offset, blob, signal)`,
`status(uploadId)`, `cancel(uploadId)`, `pending(projectId)`.

Client changes (`api/client.ts`): `ApiError` gains `details: Record<string,
unknown>` holding every field of the agent's `error` object besides `code` and
`message` (`offset`, `free_bytes` live there); `Api` gains `patch(path, body:
Blob, headers, signal)` sending the raw body as `application/octet-stream`. The
shared `send()` takes an optional external `AbortSignal`, combined with the
timeout via `AbortSignal.any`; an abort caused by that external signal rejects
with `ApiError("aborted", …, 0)`, not `unreachable`.

### `queue.ts` — `UploadQueue`

```
Item = {key, projectId, dir, name, size, fingerprint, file?, uploadId?,
        offset, replace, state, reason?, busy?, fromReload?}
state = waiting | going | paused | stalled | noRoom | failed | done
```

`fingerprint` is `name:size:lastModified`; the path sent is `dir ? dir/name :
name`.

Public surface: `subscribe`, `snapshot` (for `useSyncExternalStore`),
`add(projectId, dir, files)`, `pause(key)`, `resume(key)`, `remove(key)`,
`replace(key)`, `relink(key, file)` (returns false on a fingerprint mismatch),
`adoptPending(projectId, uploads)`, `carryOn()` (after `noRoom`). Constructor
takes the upload api, `onLanded(item)`, `onSessionLost(reason)`, and a `sleep`
so tests don't wait.

The runner takes the first `waiting` item across all projects; one item moves at
a time.

1. No `uploadId`: `start`. `file_exists` → `failed` with reason `file_exists`;
   `not_enough_space` → `noRoom`; `path_is_folder`, `path_traversal` →
   `failed`. Otherwise keep `upload_id`, `chunk_size`.
2. While `offset < size`: `patch(offset, file.slice(offset, offset +
   chunk_size))`; take `offset` from the answer.
   - `offset_mismatch` → `offset = details.offset`, continue.
   - `project_busy` when the chunk reaches `size` (the bytes have landed; only
     the finish was refused) → `busy = true`; every 3 s send an empty PATCH at
     `Upload-Offset: size` until it answers `done`.
   - `disk_full` → `offset = details.offset`, `noRoom`; the runner stops taking
     items until `carryOn()`, which re-reads `/disk` and, if the item now fits,
     sets it `waiting` and restarts.
   - `unreachable`/timeout → `status()` up to three times after 2, 4, 8 s; a
     successful read syncs `offset` and continues; after the third failure →
     `stalled`.
   - `upload_not_found` → drop `uploadId`, restart from 0 once; the second time
     → `failed`.
   - `permission_denied`, `project_not_found` → `failed` (the agent cancelled
     the staging).
   - `not_signed_in`/`session_expired` → `onSessionLost`.
3. `done` from the agent → `done`, `onLanded(item)`.

`pause` aborts the chunk in flight and sets `paused` at the last offset the agent
confirmed; `resume` sets `waiting` and the runner re-reads `status()` before the
next chunk. `remove` aborts if running and cancels on the agent when there is an
`uploadId` (a failed cancel is ignored — the sweep catches it). `adoptPending`
ignores `upload_id`s it already holds.

The queue instance is created once inside the signed-in tree in `App` and
provided by context (`uploads/QueueProvider.tsx`, `useQueue(projectId)`).
`onLanded` invalidates `["files", projectId, dir]` and, for a recognised kind,
records the prompt card.

### `kinds.ts`, `eta.ts`, `destination.ts`

- `kindOf(name) → "dump" | "archive" | null` and `promptFor(kind, path)`.
- `eta(samples, remaining)`: bytes per second over the last 20 s of `(time,
  offset)` samples; nothing until 5 s of samples exist; a result over 24 h shows
  nothing rather than "about 3 days".
- `folderNameError(name)` and `joinPath(dir, ...parts)` (no leading, trailing or
  double slash); `fits(size, freeBytes)` = `size + 1 GiB <= freeBytes`.

### Queries

`["files", id, dir]` → one level, refetched on focus and on landing, never
polled. `["disk"]` read fresh before each add (`staleTime: 0`), never polled.
Pending uploads are read by `queue.syncPending(id)` when the Files screen mounts
(no query cache: the queue is their only home).

## 5. Mocks

`mocks/handlers.ts` gains an in-memory tree per project (the board's `recipe-box`
layout: `data/`, `public/`, `src/`, `recipes-seed.csv`, `README.md`,
`package.json`), `GET /api/disk`, the upload routes and the download route. The
mock records byte counts only (never the bodies), delays each PATCH so progress
is visible, and adds the file to the tree on finish.

New `?scenario=` values:

- `uploads` — two pending uploads on the server (reload recovery, stalled rows).
- `full` — `/disk` reports 2.1 GB free (frame 13).
- `fills-up` — the third chunk of any file over 20 MB answers `disk_full`; after
  it, `/disk` reports room again (frame 14).
- `busy` — the last chunk answers `project_busy` twice before landing.
- `locked` — `data/` answers `permission_denied`.

## 6. Testing

Each test names the bug it catches. The queue tests drive the real `UploadQueue`
against a scripted fake of `uploadApi`.

| Test | The bug |
|---|---|
| `offset_mismatch` resumes from `details.offset` | Resume corrupts the file or loops |
| `project_busy` on the last chunk retries an empty PATCH at `size` until done | Upload sits at 100% and never lands |
| `disk_full` sets `noRoom` at `details.offset` and no other item starts | Next file fills the disk too; wrong progress |
| Pause aborts the chunk; resume re-reads the server offset first | Resume sends from a stale offset |
| One item at a time, in order added; remove on a started item cancels on the agent | Parallel uploads; orphaned staging |
| Network failure → `stalled` after three status retries | A spinner that never ends |
| `relink` refuses a mismatched fingerprint | Resuming with another file's bytes |
| `adoptPending` skips uploads already held | Duplicate rows after returning to Files |
| `upload_not_found` restarts from 0 once, then fails | Endless restart loop |
| Client: `details` carries `offset`/`free_bytes`; an external abort is `aborted`, not `unreachable` | Engine can't resume; pause looks like a dropped connection |
| `kindOf`: `.sql.gz`, `.tar.gz`, upper case, no extension, `archive.zip.txt` | Wrong prompt, or none |
| `folderNameError` / `joinPath`: `/`, `..`, spaces, top-level join | A traversal refusal the UI could have prevented |
| `eta`: silent before 5 s; a stall doesn't give days | Silly time-left numbers |
| `fits` at exactly `size + 1 GiB` | UI and agent disagree on the boundary |

Left untested on purpose: drag and drop, `beforeunload`, the download link
(browser glue); screen markup (walked in the browser per scenario); the mock.

## 7. Docs and branch

- `CLAUDE.md`: the dev line lists the new scenarios; the `web/` bullet gains a
  line on `uploads/queue.ts` owning the upload protocol.
- Branch `feature/web-files` from `main`.
