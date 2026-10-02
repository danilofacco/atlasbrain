# Features and operation

This guide covers detailed behavior, limits and optional setup. For the project
overview and installation, start with the [README](../README.md).

## Feature details


- **Code understanding:** tree-sitter extracts symbols, imports, calls, inheritance, and rationale comments without an LLM.
- **Graph exploration:** directed symbol relationships, BFS/DFS traversal, dependency paths, impact analysis, and architecture reports. Extracted relationships and inferred connections carry explicit provenance.
- **Visual Markdown editor:** use **New note** to choose a title, project-relative folder, tags and a note/decision/learning template. Open **Edit** in a Markdown reader to change an existing file. Switch between **Text** and **Preview**; save with Cmd/Ctrl+S, or create with Cmd/Ctrl+N. Saving updates the index and reveals the file in the graph. Unsaved edits trigger a warning before leaving. The editor checks content revisions and rejects conflicting saves; use **Reload from disk** to review the latest file. **Previous version** loads the last saved backup into the editor for review; save to restore it. Backups are local and ignored in `.atlasbrain/.editor-backups/`. The editor exposes the previous content and retains the most recent 30 replacement versions per path. Editing supports UTF-8 `.md`/`.markdown` up to 200 KB; generated/internal folders and symlinks are excluded. Code and documents remain in the reader.

- **Hybrid search:** keyword search, local multilingual embeddings, and symbol lookup across code, notes, PDFs, DOCX, and HTML. For implementation questions in small projects with fully embedded code, a lightweight second stage reorders only the first three candidates using their strongest code-chunk similarity; exact matches stay first.
- **Persistent memory:** decisions and learnings stored in Markdown, including links between superseded decisions and their replacements.
- **URL imports:** save public web pages and text-based PDFs as searchable Markdown snapshots with the original URL, retrieval date, and content hash. Repeated imports reuse the same note.
- **Local interface:** browse the graph, search, read notes, and switch projects in your browser. The default **2D** view arranges the nodes into a brain seen from the side. **3D** gives the cortex, cerebellum, and stem bounded volume, a subtle rotating contour, and depth-aware contrast; drag to rotate it. In 2D and 3D, a restrained light pulse travels from the cortex center toward outer files, lighting both nodes and connections while no node is selected or hovered; reduced-motion settings disable it. The pulse starts disabled by default. The pulse icon next to graph settings toggles the effect in both 2D and 3D, with a per-project preference. Idle 3D rotates automatically, pausing on selection, hover, dragging, open dialogs or hidden tabs. **Orbits** groups communities into a radial map. These three modes are mutually exclusive. Large projects show real file nodes from the start. Each page defaults to 500 file nodes, adjustable from 50 to 800 under **Graph controls → Nodes per page** and saved per project, distributed across project folders with decisions and highly connected files first; page through the rest without overloading the browser. Search opens the page containing a selected result. Node size adjusts to the number of nodes, staying readable in small graphs and compact in dense ones. In 3D, drag to rotate and Shift + drag to pan; use the wheel or zoom buttons in every mode. Isolated files are red and tags are yellow. The **Isolated** tab lists all isolated indexed files with pagination and review suggestions; the `isolated_files` MCP tool exposes the same report for project-scoped connections and aggregates registered projects when connected to the global brain. Isolation uses the complete index, including tags, semantic similarity and pending links, regardless of visual filters. It does not imply a file is unused. Closing a note keeps its node selected and its connections animated; click the background or press Escape again to clear the selection. Hover a node to highlight its neighbors and illuminate its connections like optical fibers, with an elongated band of light travelling along each highlighted line. Nearby labels fade with cursor distance, with a cap of eight and overlap avoidance; hovering a node limits those labels to its neighbors. Light is visual feedback, not data-flow direction. Reduced-motion settings keep the light steady. Depth and brain placement are visual arrangements, not measures of semantic distance; dependency diagrams retain their 2D layout.
- **Task context and index health:** `task_context(task, tokens=2000)` collects relevant files, active decisions, incoming/outgoing relationships, evidence and freshness warnings. `index_status` lists pending files without reindexing; `changes` compares the two latest meaningful snapshots. Reports support bounded output, pagination where applicable, and follow-up reads. Token budgets use a conservative character ceiling, not exact model tokenization.
- **Reviewable connections:** `suggest_links` offers candidates based on file names and folders, with heuristic confidence and `INFERRED` evidence. `record_link` records a reviewed relationship as `DECLARED` in `.atlasbrain/Vinculos/`. Suggestions never apply automatically; declared relationships are not extracted code dependencies. The Soltos view provides the same review flow.
- **Graph exploration:** click a node for a compact selection panel; open its content or connections when needed. Filter by folder, file extension/language, file type, relationship and provenance. The browser remembers mode, filters, camera and layout per project. The Changes view lists added/edited/removed files and relationships; graph overlays highlight additions and changes, and removed relationships when both endpoints remain visible. History begins after the feature is enabled, retaining the two latest different index versions rather than a complete audit log.

- **Engine maintenance:** inspect and recover interrupted renames, refresh stale extractive summaries, evaluate search with labelled project questions, and review automatic memory capture. Embedding results are cached by model and content. See [engine reliability and evaluation](engine-reliability.md).

- **One shared service:** all MCP clients and the web interface use one persistent Python process and one HTTP port. Each project has a separate endpoint.

```text
my-project/
├── .atlasbrain/
│   ├── Decisions/          # decisions stored locally
│   ├── Learnings/     # learnings
│   ├── Imports/      # web/PDF snapshots with source metadata
│   ├── REPORT.md      # generated architecture report
│   └── index.db          # local SQLite index
└── your code and documents
```

## Service lifecycle and updates

The default address is `http://127.0.0.1:8765`. The interface and MCP endpoints share it. Project endpoints are derived from the folder's absolute path, so a client's working directory cannot silently select the wrong project.

```bash
uv run atlasbrain status
uv run atlasbrain serve --vault /absolute/path/to/my-project
uv run atlasbrain stop
uv run atlasbrain start --vault /absolute/path/to/my-project
```

`setup`, `start`, and `serve` reuse a verified live service. File locks serialize concurrent starts and prevent duplicate daemons. If another application owns the port, startup reports the conflict; it does not pick extra ports or terminate that application. You can choose a different port with `--port 8766` on `setup`/`start`/`serve`, using the same value thereafter.

The service survives closing the terminal. **After restarting your computer, run `start` again before using MCP.** State and logs are in `~/.config/atlasbrain/server.json` and `server.log`. After a manual checkout update, restart the service and reconnect your clients to discover newly added tools.

### Automatic version updates

Automatic updates are on by default in every Git clone that runs the AtlasBrain service; no setup command is needed. To inspect or check for a release:

```bash
uv run atlasbrain update check   # check now
uv run atlasbrain update status  # last result
```

While the service runs, it checks the configured upstream of `main` or `master` every six hours. A higher stable `project.version` in the remote `pyproject.toml` triggers a fast-forward update, `uv sync --locked --inexact`, and a restart on the same port. The inexact sync preserves optional packages already installed in the environment. Publishing a new version means committing its version bump and matching `uv.lock` to the upstream branch; ordinary commits at the same version are not installed. Automatic updates require a clean clone. Local changes, untracked files, a divergent branch, missing lockfile, or an installation outside Git block the update and remain untouched. If dependency installation or startup fails, the updater restores the previous commit and environment when the checkout is still clean, then restarts the old service. A temporary helper manages the restart; it does not open another port or keep a second daemon. `uv run atlasbrain update disable` turns off periodic checks; `uv run atlasbrain update enable` turns them back on; `uv run atlasbrain update apply` checks and applies immediately. Results are saved in `~/.config/atlasbrain/update-status.json`. New MCP tool names still require reconnecting the client after a restart.

“One process” refers to the persistent MCP/web service. Installation, Git scanning, explicit CLI commands, and optional transcript capture can use temporary processes. The legacy `atlasbrain mcp` command uses stdio and starts a separate server per client; use the generated HTTP configuration for the shared service. Remove existing stdio entries for atlasbrain when migrating, and close their old client sessions.

## Language coverage

AST extraction covers Python; JavaScript/JSX/MJS/CJS; TypeScript/TSX; Go; Rust; Java; Ruby; PHP; Swift; Kotlin/KTS; C; C++; C#; Bash; Lua; Scala; Dart; **Elixir, Julia, R, Haskell, OCaml, Perl, and PowerShell**.

Extraction varies by grammar. Import alias resolution is deepest for Python and JavaScript/TypeScript; unresolved external packages and ambiguous references are not presented as proven symbol connections. Other text/configuration files can still be searched without AST extraction.

## Optional assistant hooks

From the cloned AtlasBrain repository, install global hooks for Claude Code and Codex:

```bash
uv run atlasbrain hooks
# Remove AtlasBrain hooks and instruction blocks:
uv run atlasbrain hooks --remover
```

Restart/reload your clients after installation. In Codex, open `/hooks` and review/trust the new or changed AtlasBrain hooks; Codex skips untrusted hooks.

This merges entries into `~/.claude/settings.json` and `~/.codex/hooks.json`, preserving other hooks. It also updates `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`. The installed commands point to this checkout's environment; keep the checkout in place and run the installer again if you move it.

`PreToolUse` adds a reminder to consult AtlasBrain before terminal searches or file reads, at most once every 20 minutes per project/session. It only runs in projects with `.atlasbrain/`, lets tools continue, and does not start an MCP server or load embedding models. Claude uses `Bash|Read|Grep|Glob`; Codex exposes shell execution as `Bash`. Normal tools remain available when MCP is unavailable. `SessionStart` supplies project context, and session completion captures relevant memory.

MCP instructions, installed instruction blocks and the reminder ask the agent to start substantive tasks with `task_context`, then use `read` or `read_many` to review relevant active decisions and learnings before changing behavior. The agent should briefly name the notes it actually consulted and explain how they affected its work, distinguishing excerpts from section or full-note reads. Stored memory remains reference material; the user's current instructions take precedence.

The session briefing includes indexed excerpts and exact note paths, with current-project memory first and historical decisions excluded. It stays within 4,000 characters and reports entries omitted by that budget. These excerpts help discover memory; they do not establish a full-note review. This workflow guides the agent without blocking tools or guaranteeing compliance by every client. After updating AtlasBrain, reconnect MCP clients to refresh server instructions and rerun `uv run atlasbrain hooks` to refresh existing global instruction blocks while preserving unrelated settings.

Hook formats: [Claude Code](https://code.claude.com/docs/en/hooks), [Codex](https://developers.openai.com/codex/hooks).

## Connected Markdown editing


Type `[[` in the visual editor to find files across the full index. Choose a suggestion with the arrow keys and Enter/Tab, or click it. Targets use project paths to distinguish duplicate filenames. The editor includes backlinks and candidate links that you can read before adding to the draft. Saving a note shows candidate connections for review. Click an unresolved wiki link in the reader or preview to create its note; the file is only written when you save.

## Reliable memory updates

Markdown writes now share atomic saving, revision checks and a recoverable history of the last 30 replacements per file. Use the history icon in the reader to compare/restore versions of existing files. Deletion is permanent: after confirmation, the file and its local editor recovery versions are removed.

MCP clients can use `edit_section`, `note_history`, `restore_note`, `review_memory` and `review_decision`. `read` returns a revision; update tools accept `revision`, and write tools accept `operation_id` for safe retries. When a new decision supersedes an older one, pass `supersedes` while registering it; use `review_decision` with exact paths to reconcile decisions already saved. The old record stays available as history but leaves active task context. The shared daemon groups external changes before incremental indexing. Search prioritizes exact names and labels historical decisions and matching signals. See [reliable memory behavior and limits](reliable-memory.md).

## Automatic consolidation

MCP writes trigger bounded extractive memory consolidation once a project has 40 eligible annotations. Each pass groups at most six sources into a linked synthesis; original files remain intact. Use `plan_consolidation` and `consolidate_memory` to review groups or supply an agent-written summary with checked source revisions. Changed sources invalidate the summary's search priority. This reduces repeated context, not physical file count. See [memory consolidation](memory-consolidation.md).

Task context now accepts explicit `focus` files and an `objective`, returns active decisions and unchecked tasks, and explains budget omissions. `rename_note` previews Markdown moves, then applies a checked plan with wikilink/relative-link updates and a stable note identifier. See [task context and renaming](task-context-and-renaming.md) for scope and recovery limits.

## Language and MCP compatibility

The visual interface defaults to English. Select PT-BR in the top bar to switch to Brazilian Portuguese without closing your draft. The language preference is saved in your browser. File contents, titles, paths and existing memory metadata retain their original language and format.

MCP discovery exposes English tool names, parameters and instructions. Legacy Portuguese names remain callable but are hidden from discovery. English calls use English response fields, such as `items`, `has_more`, `plan_revision` and `recovery_revision`; source content is preserved. Reconnect clients to refresh cached tool lists. Generated project instructions and hook hints refer to the English names.

The web viewer and MCP share port **8765** by default: [http://127.0.0.1:8765](http://127.0.0.1:8765).
