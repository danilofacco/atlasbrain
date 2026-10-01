# Reliable Markdown memory

All normal Markdown writes (visual editor, MCP notes, decision/learning capture, URL imports and reviewed links) now share atomic replacement and optimistic revision checks. Automatic registration also serializes its read/modify/write cycle across cooperating threads and processes. External editors do not participate in the lock: revision checks detect changes before replacement, but this is not a cross-application filesystem transaction.

## Updating memory

- `read` returns the full Markdown file's SHA-256 revision, including when reading one section.
- `append_note` and `update_note` accept `revision`. Supplying it rejects stale updates instead of silently overwriting them.
- `edit_section(note, section, text, revision, operation_id)` replaces one H2 section, preserves other sections and frontmatter, ignores fenced-code headings and rejects ambiguous duplicate headings. A missing section is appended.
- Creation, appending, decision/learning registration, note updates and section edits accept a stable `operation_id` identifier. Repeat it with identical arguments after a network failure. Reusing it for different arguments fails.
- Retry receipts are persisted locally. An interrupted operation with an unfinished receipt is blocked: inspect the file before deliberately starting a new operation. This prevents blind replay but does not promise automatic recovery from every crash.
- Explicit decision replacement continues to link the new and previous decisions. `review_memory` reports numeric disagreements between active decisions with the same normalized title and lists replacement links. These are **INFERRED review candidates**, not proof of contradictions or a general semantic contradiction detector.
- When both decisions already exist, `review_decision(previous, action="supersede", replacement=..., reason=...)` marks the predecessor as superseded and links to the active successor. `action="revoke"` retires a decision without a successor. Both references must be exact indexed Markdown paths; the reason is required. The operation updates only the predecessor, keeping the successor and original text intact. It never infers that similar notes contradict one another.
- Historical decisions remain searchable with an explicit status label, but `task_context` excludes them from automatic candidates and relationship lists. If one is explicitly focused, the response identifies it as historical and includes its active successor when available. The session briefing and `decisions` already list active decisions by default.

## History and recovery

Every replacement preserves the previous content; the most recent 30 versions per path are retained in `.atlasbrain/.editor-backups/`, excluded from indexing and Git. Existing notes acquire history on their next change; earlier edits cannot be reconstructed. The generated architecture report uses atomic replacement without version history.

In the reader, the history icon lists versions, shows a diff against the current file and restores the selected version with a revision check. MCP clients can use `note_history` and `restore_note`. The editor/history preview supports UTF-8 Markdown up to 200 KB. URL imports retain their existing 2 MB indexing limit; larger historical payloads remain on disk but cannot be previewed in this editor.

File deletion is permanent after confirmation: it unlinks the file and deletes its local editor backup/version history. There is no trash or deleted-file restore API. This is filesystem deletion, not secure erasure of storage or removal from Git/external backups. Legacy trash copies created before this change are not automatically purged. Replacement of two linked decision files is not a single atomic transaction.

## Incremental indexing

The shared HTTP daemon uses one existing background thread for all registered projects. A polling queue checks metadata once per minute and processes detected changes in that same pass, grouping external edits made between checks. Only files whose metadata/content changed are reprocessed by the existing indexer. A reconciliation runs at least every 5 minutes while the worker is available. Skipped/failed index calls remain pending for the next pass. There is no additional listening port or persistent process. Large projects can make a polling pass take longer than its nominal interval.

Explicit writes still request an immediate index update; the queue covers external edits, creates, renames and deletes. Global graph relationships may still need rebuilding when documents change. Metadata polling is not an OS filesystem watcher and cannot detect an external edit that preserves both timestamp and size until a forced reindex.

## Search

Exact full paths, filenames, titles and symbol names are prioritized while preserving folder/tag/type/date filters. Historical decisions are downweighted, not hidden. Use `status:substituída` to explicitly search that state. Exact historical lookups remain possible. Results expose their signals (keyword, semantic, structural or exact) and historical status; these explanations are not calibrated confidence scores.

`python -m benchmarks.evaluate_search_updates` reproduces the comparison against the existing 20 answerable synthetic test questions in an isolated vault. Before/after Top1 was **95% / 95%**, MRR **0.975 / 0.975**. This checks regression on that fixture, not a general precision improvement. Dedicated tests cover exact lookup, current decisions and hard filters.

Restart the service after upgrading backend code. Reconnect MCP clients to discover the new tools and arguments. No automatic client configuration or restart is performed by these features.
