# Task context and safe Markdown renaming

These features are available through MCP; the visual interface is unchanged.

## Task context

`task_context(task, limit=8, tokens=2000, focus=None, objective="implement")`:

- `focus`: up to five explicit file references, considered before search candidates.
- `objective`: `implement`, `investigate`, `review` or `document`. Review favors incoming relationships; other goals favor outgoing relationships. Extracted/declared evidence precedes inferred relationships.
- Includes matching excerpts, selection reasons, indexed hashes, active decisions and explicit unchecked Markdown tasks from selected notes. Completed checkboxes are excluded. It does not infer an entire project's backlog.
- Superseded or revoked decisions do not consume automatic context slots. An explicitly focused historical decision is reported under `historical_decisions` with its status; its active successor is selected when known.
- Includes index freshness and stale-synthesis warnings. `selected` and `omitted_by_budget` show what was selected and how many entries were removed to fit.
- Budget is a conservative character ceiling (three characters per requested token), not tokenizer-accurate billing. Small budgets may return only a limit warning and omission counts. Use `read` with returned references to expand selected evidence.

## Rename or move a note

1. Call `rename_note(note="notes/Old.md", destination="archive/New.md")`.
2. Inspect `affected_files` and `plan_revision`.
3. Repeat with the returned `plan_revision` to apply the move.

Both paths must refer to Markdown inside the project. Destinations cannot overwrite existing files; traversal, internal paths and symlinks are rejected. The tool refreshes the index first. Changed plan content invalidates the preview.

Resolved wikilinks retain heading anchors and display aliases. Local Markdown links to the note are rewritten, and relative outgoing links/images inside a moved note are rebased. Inline/fenced code examples are left untouched. The note's existing title is retained, and a persistent `atlasbrain_id` is added if absent. The existing database note ID is retained during the move. Reviewed-link metadata and consolidation source paths are updated; changed source bytes still invalidate old syntheses.

Scope is **indexed Markdown** parsed by AtlasBrain's current Markdown link syntax. It does not rewrite source-code references, external documents, unindexed notes, HTML links or Markdown reference-style links. Files over the editor's 200 KB limit block the plan rather than being silently skipped; total affected content is capped at four million characters.

The new destination is written before backlinks and the old path is removed last. A private recovery journal in `.atlasbrain/.editor-backups/rename-*.json` preserves before/after content. Stale writes are rejected. An interrupted multi-file operation may leave both paths and partially updated references; the error identifies its recovery journal. This is not a multi-file filesystem transaction or automatic rollback. Inspect it before retrying. The new path gets the immediate pre-move content as a recoverable version; older path-specific history remains stored under the old path.
