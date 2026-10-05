# Design boards

`eggie-web-ui.dc.html` is a snapshot of `Eggie Web UI.dc.html` from the
claude.ai/design project `5a77e605-5a2a-4a68-a1e7-76c07c0d8aa2`, taken
2026-09-22. The project stays the source of truth; refresh this copy when the
board changes. It will not render on its own (it expects the design tool's
`support.js` runtime) — read it as markup: tokens are in the `<style>` block at
the top, and every frame is a plain inline-styled `<div>`.

| Frame | Screen | Part |
|---|---|---|
| 01 | Nothing here yet (empty list) | C |
| 02 | Projects list, four states | C |
| 03 | Discovered folders band | C |
| 04 | Project detail, running | C |
| 05 | Starting, first run | C |
| 06 | Something's wrong | C |
| 07 | Files | D |
| 08 | Upload destination | D |
| 09 | Uploads in flight | D |
| 10 | Upload done + prompt card | D |
| 11 | Analyze | C |
| 12 | Delete a project | C |
| 13 | Won't fit, before upload | D |
| 14 | Ran out of room mid-upload | D |
| 15 | Not signed in | B |
| 16 | Projects at 1200 px | B (layout), C |
| strip | State badges, sync marker, prompt card | B (kit) |

What the board shows but the product does not do is listed in
`docs/superpowers/specs/2026-09-21-web-ui-agent-prerequisites-design.md`,
section 1 ("Scope cuts"); what each part inherits from the agent is in
section 8.
