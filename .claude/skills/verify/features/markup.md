# Markup

The user draws markups (shapes, text, stamps, highlights) on a page. They select a markup to move, resize or edit it in the Properties panel (subject, comment, status, replies), delete it, undo, and save so the markups stay in the PDF. The Markups List at the bottom shows one row per markup. Covers manual test plan section C.

## Sub-features

- `draw`: every tool in the Shapes, Text & Review, and Stamp & Sign groups places a markup.
- `select-move-resize`: the Select tool picks a markup. Dragging moves it, and dragging the corner handle resizes it.
- `properties`: Subject, Comment and Status edits in the Properties panel, and replies.
- `delete-undo`: Delete key or Edit > Delete markup, then Edit > Undo / Redo (Ctrl+Z / Ctrl+Y).
- `copy-paste`: Edit > Copy / Paste / Duplicate markup (Ctrl+C / Ctrl+V / Ctrl+D).
- `context-menu`: right-click a markup for status, duplicate, reply and delete.
- `persist`: File > Save writes the markups into the PDF, and another reader sees them.

## How to get to it (user POV)

- Tool group drop-downs on the toolbar (Shapes, Text & Review, Stamp & Sign, Insert, Measure), or single-key shortcuts: R rectangle, E ellipse, L line, A arrow, C cloud, P pen, T text, O callout, N note, I highlight, S stamp, V select, H pan.
- The Tool Chest panel (right side).
- The Properties panel (right side) and the Markups List (bottom) for edits.

## Driving it with orv

Preconditions:

- `s.launch(s.samples() / "plan_v1.pdf")`. The Markups List is empty.

- **Draw.** `s.tool("Rectangle")` (or `s.key("R", s.w.cv)`), then `s.drag(0, (520, 260), (640, 340))`. `s.table(s.w.mk_table)` has one row whose Subject is `Rectangle` and Type is `Square`. The tab text becomes `*plan_v1.pdf`.
- **Select.** `s.tool("Select")`, then `s.click(0, 580, 300)`. `s.w.cv.selected` is set, and the Properties panel heading reads `Square on page 1`.
- **Subject.** `s.type(s.w.p_subject, "Wall check")`. The Markups List Subject column reads `Wall check`.
- **Status.** `s.w.p_status.setFocus()`, then `s.key("Down", s.w.p_status)`. The Status column reads `Accepted`. (The order is `(none)`, Accepted, Rejected, Cancelled, Completed, Reviewed.)
- **Reply.** `s.type(s.w.p_reply, "Moved per RFI 12")`. `s.w.p_replies.count() == 1`.
- **Delete and undo.** `s.w.cv.setFocus()`, `s.key("Delete", s.w.cv)`, then `s.menu("Edit > Undo")`. The row disappears and comes back with its subject and status.
- **Save and read back.** `s.menu("File > Save")`. The status reads `Saved` and the tab loses its `*`. Then `s.cli("csv", pdf, out)`: exit 0, and the CSV row has `subject`, `status` and `replies` (`<author>: Moved per RFI 12`).
- **Proof.** `s.shot` after drawing, after editing, and after saving. `s.keep(pdf)` and `s.keep(out)`.

## Gotchas

- Drawing tools fall back to Select after one placement. Only Count stays active. Call `s.tool(...)` again before each new markup.
- The Type column shows the PDF annotation type (`Square`, `Line`, `Polygon`), not the tool name. The Subject defaults to the tool name.
- Author is the OS user name, so it differs between machines. Never assert it.
- Save drops the selection and renumbers objects. Select the markup again after a save before editing it.
- Text, Callout, Note, Link and Image tools open input dialogs. Queue `s.answer(...)` for them first, or the run fails on an unscripted dialog.
