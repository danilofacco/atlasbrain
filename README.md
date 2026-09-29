# AtlasBrain

**A local second brain that connects your code, knowledge, and project memory to AI assistants through MCP.**

AtlasBrain combines Markdown knowledge management inspired by Obsidian with local code graphs. Search your project, understand dependencies, trace the impact of changes, and preserve decisions across conversations with Claude Code, Antigravity, and Codex.

It runs independently of Obsidian. Your notes are ordinary Markdown, so you can also open them in Obsidian or any editor.

Project data lives in `.atlasbrain/`, and shared service state lives in `~/.config/atlasbrain/`. The Python package, CLI command, and MCP entry are all named `atlasbrain`.

## Preview

![AtlasBrain's 2D side-view brain graph](docs/media/graph-2d.png)

| 3D brain graph | Decision and learning timeline |
| --- | --- |
| ![AtlasBrain's 3D graph](docs/media/graph-3d.png) | ![AtlasBrain's decision timeline](docs/media/decisions.png) |

The light pulse illuminates both nodes and connections when the graph is idle. The 3D view can be rotated with a drag.

| 2D cortex pulse | 3D rotation |
| --- | --- |
| ![Animated light pulse across the 2D brain](docs/media/graph-pulse-2d.gif) | ![Animated rotation of the 3D brain graph](docs/media/graph-rotate-3d.gif) |

## What it does

- **Code understanding:** tree-sitter extracts symbols, imports, calls, inheritance, and rationale comments without an LLM.
- **Graph exploration:** directed symbol relationships, BFS/DFS traversal, dependency paths, impact analysis, and architecture reports. Extracted relationships and inferred connections carry explicit provenance.
- **Visual Markdown editor:** use **Nova nota** to choose a title, project-relative folder, tags and a note/decision/learning template. Open **Editar** in a Markdown reader to change an existing file. Switch between **Texto** and **Prévia**; save with Cmd/Ctrl+S, or create with Cmd/Ctrl+N. Saving updates the index and reveals the file in the graph. Unsaved edits trigger a warning before leaving. The editor checks content revisions and rejects conflicting saves; use **Recarregar do disco** to review the latest file. **Versão anterior** loads the last saved backup into the editor for review; save to restore it. Backups are local and ignored in `.atlasbrain/.editor-backups/`, retaining one previous version per file. Editing supports UTF-8 `.md`/`.markdown` up to 200 KB; generated/internal folders and symlinks are excluded. Code and documents remain in the reader.

- **Hybrid search:** keyword search, local multilingual embeddings, and symbol lookup across code, notes, PDFs, DOCX, and HTML. For implementation questions in small projects with fully embedded code, a lightweight second stage reorders only the first three candidates using their strongest code-chunk similarity; exact matches stay first.
- **Persistent memory:** decisions and learnings stored in Markdown, including links between superseded decisions and their replacements.
- **URL imports:** save public web pages and text-based PDFs as searchable Markdown snapshots with the original URL, retrieval date, and content hash. Repeated imports reuse the same note.
- **Local interface:** browse the graph, search, read notes, and switch projects in your browser. The default **2D** view arranges the nodes into a brain seen from the side. **3D** gives the cortex, cerebellum, and stem bounded volume, a subtle rotating contour, and depth-aware contrast; drag to rotate it. In 2D and 3D, a restrained light pulse travels from the cortex center toward outer files, lighting both nodes and connections while no node is selected or hovered; reduced-motion settings disable it. **Órbitas** groups communities into a radial map. These three modes are mutually exclusive. Large projects show real file nodes from the start. Each page renders up to 300 files, distributed across project folders with decisions and highly connected files first; page through the rest without overloading the browser. Search opens the page containing a selected result. Node size adjusts to the number of nodes, staying readable in small graphs and compact in dense ones. In 3D, drag to rotate and Shift + drag to pan; use the wheel or zoom buttons in every mode. Isolated files are red and tags are yellow. The **Soltos** tab lists all isolated indexed files with pagination and review suggestions; the `soltos` MCP tool exposes the same report for project-scoped connections and aggregates registered projects when connected to the global brain. Isolation uses the complete index, including tags, semantic similarity and pending links, regardless of visual filters. It does not imply a file is unused. Closing a note keeps its node selected and its connections animated; click the background or press Escape again to clear the selection. Hover a node to highlight its neighbors and illuminate its connections like optical fibers, with an elongated band of light travelling along each highlighted line. Nearby labels fade with cursor distance, with a cap of eight and overlap avoidance; hovering a node limits those labels to its neighbors. Light is visual feedback, not data-flow direction. Reduced-motion settings keep the light steady. Depth and brain placement are visual arrangements, not measures of semantic distance; dependency diagrams retain their 2D layout.
- **Task context and index health:** `contexto_tarefa(tarefa, tokens=2000)` collects relevant files, active decisions, incoming/outgoing relationships, evidence and freshness warnings. `estado_indice` lists pending files without reindexing; `mudancas` compares the two latest meaningful snapshots. Reports support bounded output, pagination where applicable, and follow-up reads. Token budgets use a conservative character ceiling, not exact model tokenization.
- **Reviewable connections:** `sugerir_vinculos` offers candidates based on file names and folders, with heuristic confidence and `INFERRED` evidence. `registrar_vinculo` records a reviewed relationship as `DECLARED` in `.atlasbrain/Vinculos/`. Suggestions never apply automatically; declared relationships are not extracted code dependencies. The Soltos view provides the same review flow.
- **Graph exploration:** click a node for a compact selection panel; open its content or connections when needed. Filter by folder, file extension/language, file type, relationship and provenance. The browser remembers mode, filters, camera and layout per project. The Mudanças view lists added/edited/removed files and relationships; graph overlays highlight additions and changes, and removed relationships when both endpoints remain visible. History begins after the feature is enabled, retaining the two latest different index versions rather than a complete audit log.

- **Engine maintenance:** inspect and recover interrupted renames, refresh stale extractive summaries, evaluate search with labelled project questions, and review automatic memory capture. Embedding results are cached by model and content. See [engine reliability and evaluation](docs/engine-reliability.md).

- **One shared service:** all MCP clients and the web interface use one persistent Python process and one HTTP port. Each project has a separate endpoint.

```text
my-project/
├── .atlasbrain/
│   ├── Decisões/          # decisions stored locally
│   ├── Aprendizados/     # learnings
│   ├── Importações/      # web/PDF snapshots with source metadata
│   ├── RELATORIO.md      # generated architecture report
│   └── index.db          # local SQLite index
└── your code and documents
```

## Install and connect

Requirements: Git and [uv](https://docs.astral.sh/uv/getting-started/installation/), on macOS or Linux. Windows users can run the service in WSL, with clients able to reach its localhost port. Native Windows is currently unsupported. `uv` downloads Python 3.12 and installs the project's dependencies automatically.

### 1. Clone

Clone the public repository:

```bash
git clone https://github.com/danilofacco/atlasbrain.git
cd atlasbrain
```

### 2. Generate your MCP configuration

Replace `/absolute/path/to/my-project` with the folder you want to index. It can be a code repository or a folder of notes. Choose your client:

```bash
uv run --python 3.12 atlasbrain setup --vault /absolute/path/to/my-project --client claude
# Or: --client antigravity
# Or: --client codex
```

This installs dependencies, creates the project's `.atlasbrain/`, starts or reuses the shared service, and prints the configuration to paste into your client. No global Python install, symlink, API key, or manually installed dependency is needed. It does not edit your client's configuration automatically.

The first index downloads the local embedding model and can take longer. The service is available while indexing continues. To disable embeddings before starting it, set `ATLASBRAIN_NO_EMBED=1`; keyword search and code graphs still work.

### 3. Paste the configuration

| Client | Configuration location | Generated format |
| --- | --- | --- |
| Claude Code | `.mcp.json` in your target project | `mcpServers` with `type: "http"` and `url` |
| Antigravity | **MCP Servers → Manage MCP Servers → View raw config**, or `~/.gemini/config/mcp_config.json` | `mcpServers` with `serverUrl` |
| Codex app / CLI / IDE | `~/.codex/config.toml`, or `.codex/config.toml` in a trusted project | `[mcp_servers."atlasbrain"]` with `url` |

Merge the generated entry into an existing configuration rather than replacing other servers. Reconnect/reload the MCP client after changing its configuration. These Claude instructions are for **Claude Code**. For Claude Desktop chat, follow the [local bridge instructions](docs/claude-desktop.md) to reach the same service.

Official instructions: [Claude Code](https://code.claude.com/docs/en/mcp), [Antigravity](https://antigravity.google/docs/mcp), [Codex](https://developers.openai.com/codex/mcp/).

To connect another client to the same project, run `setup` again with its client name: it reuses the same PID and port. To connect another project, change `--vault`. For multiple project entries in one client, also use `--nome my-project` to give each entry a distinct name.

A project-specific MCP URL already fixes the project folder. On a global AtlasBrain connection, use `projetos` to list registered folders and pass the absolute folder as `pasta_projeto` on each project tool call. Without it, project tools ask for a folder instead of returning data from the global brain by mistake; `soltos` alone can list isolated files across all registered projects.

## One process, one port

The default address is `http://127.0.0.1:8765`. The interface and MCP endpoints share it. Project endpoints are derived from the folder's absolute path, so a client's working directory cannot silently select the wrong project.

```bash
uv run atlasbrain status
uv run atlasbrain serve --vault /absolute/path/to/my-project
uv run atlasbrain stop
uv run atlasbrain start --vault /absolute/path/to/my-project
```

`setup`, `start`, and `serve` reuse a verified live service. File locks serialize concurrent starts and prevent duplicate daemons. If another application owns the port, startup reports the conflict; it does not pick extra ports or terminate that application. You can choose a different port with `--porta 8766` on `setup`/`start`/`serve`, using the same value thereafter.

The service survives closing the terminal. **After restarting your computer, run `start` again before using MCP.** State and logs are in `~/.config/atlasbrain/server.json` and `server.log`. After a manual checkout update, restart the service and reconnect your clients to discover newly added tools.

### Automatic version updates

Automatic updates are on by default in every Git clone that runs the AtlasBrain service; no setup command is needed. To inspect or check for a release:

```bash
uv run atlasbrain update check   # check now
uv run atlasbrain update status  # last result
```

While the service runs, it checks the configured upstream of `main` or `master` every six hours. A higher stable `project.version` in the remote `pyproject.toml` triggers a fast-forward update, `uv sync --locked --inexact`, and a restart on the same port. The inexact sync preserves optional packages already installed in the environment. Publishing a new version means committing its version bump and matching `uv.lock` to the upstream branch; ordinary commits at the same version are not installed. Automatic updates require a clean clone. Local changes, untracked files, a divergent branch, missing lockfile, or an installation outside Git block the update and remain untouched. If dependency installation or startup fails, the updater restores the previous commit and environment when the checkout is still clean, then restarts the old service. A temporary helper manages the restart; it does not open another port or keep a second daemon. `uv run atlasbrain update disable` turns off periodic checks; `uv run atlasbrain update enable` turns them back on; `uv run atlasbrain update apply` checks and applies immediately. Results are saved in `~/.config/atlasbrain/update-status.json`. New MCP tool names still require reconnecting the client after a restart.

“One process” refers to the persistent MCP/web service. Installation, Git scanning, explicit CLI commands, and optional transcript capture can use temporary processes. The legacy `atlasbrain mcp` command uses stdio and starts a separate server per client; use the generated HTTP configuration for the shared service. Remove existing stdio entries for atlasbrain when migrating, and close their old client sessions.

## Ask your assistant

- “Find where authentication is implemented and explain its dependencies.”
- “What would be affected if I changed this function?”
- “Show the path between this service and the database layer.”
- “Record this architectural decision and why we chose it.”
- “Import this documentation URL into the project's brain.”

Tool names currently use Portuguese:

| Purpose | MCP tools |
| --- | --- |
| Locate and explain code | `arquivos`, `onde`, `explicar`, `relatorio` |
| Explore relationships | `consultar_grafo`, `impacto`, `caminho`, `mapa` |
| Retrieve knowledge | `buscar`, `ler`, `ler_varios`, `relacionados`, `tags`, `recentes`, `filtrar`, `decisoes` |
| Preserve and import knowledge | `criar_nota`, `anexar`, `registrar_decisao`, `revisar_decisao`, `registrar_aprendizado`, `atualizar_nota`, `importar_url`, `reindexar` |

URL imports fetch a single page or PDF; they do not crawl entire websites, run JavaScript, bypass authentication, or OCR scanned documents. Public HTTP(S) URLs are accepted; private and loopback addresses are rejected. Import only material you are entitled to store. Imported text is reference material, not instructions for your assistant.

```bash
uv run atlasbrain importar-url --vault /absolute/path/to/my-project https://example.com/article
# Refresh the same snapshot later:
uv run atlasbrain importar-url --vault /absolute/path/to/my-project https://example.com/article --atualizar
```

## Language coverage

AST extraction covers Python; JavaScript/JSX/MJS/CJS; TypeScript/TSX; Go; Rust; Java; Ruby; PHP; Swift; Kotlin/KTS; C; C++; C#; Bash; Lua; Scala; Dart; **Elixir, Julia, R, Haskell, OCaml, Perl, and PowerShell**.

Extraction varies by grammar. Import alias resolution is deepest for Python and JavaScript/TypeScript; unresolved external packages and ambiguous references are not presented as proven symbol connections. Other text/configuration files can still be searched without AST extraction.

## Local data and optional features

Indexes and embedding inference run locally. MCP tools return requested project content to your connected assistant, subject to that assistant's own data policies. The core needs no paid API or external database; the first dependency/model download and URL imports need internet access.

Project memory in `.atlasbrain/` stays on your machine and is ignored by Git by default, including notes, imports, backups, and the generated index. Add paths to `.atlasbrainignore` to exclude them from indexing. Optional `init` Git hooks and `hooks` transcript capture are separate from the minimal installation and can launch temporary commands.

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

Hook formats: [Claude Code](https://code.claude.com/docs/en/hooks), [Codex](https://developers.openai.com/codex/hooks).

## Development

```bash
uv sync --locked
uv run pytest -q
# Optional viewer interaction checks (requires Node.js for development only):
node --test tests/viewer.test.cjs
```

Licensed under [MIT](LICENSE).

### Connected Markdown editing

Type `[[` in the visual editor to find files across the full index. Choose a suggestion with the arrow keys and Enter/Tab, or click it. Targets use project paths to distinguish duplicate filenames. The editor includes backlinks and candidate links that you can read before adding to the draft. Saving a note shows candidate connections for review. Click an unresolved wiki link in the reader or preview to create its note; the file is only written when you save.

### Reliable memory updates

Markdown writes now share atomic saving, revision checks and a recoverable history of the last 30 replacements per file. Use the history icon in the reader to compare/restore versions of existing files. Deletion is permanent: after confirmation, the file and its local editor recovery versions are removed.

MCP clients can use `editar_secao`, `historico_nota`, `restaurar_nota`, `revisar_memoria` and `revisar_decisao`. `ler` returns a revision; update tools accept `revisao`, and write tools accept `operacao` for safe retries. When a new decision supersedes an older one, pass `substitui` while registering it; use `revisar_decisao` with exact paths to reconcile decisions already saved. The old record stays available as history but leaves active task context. The shared daemon groups external changes before incremental indexing. Search prioritizes exact names and labels historical decisions and matching signals. See [reliable memory behavior and limits](docs/reliable-memory.md).

### Automatic consolidation

MCP writes trigger bounded extractive memory consolidation once a project has 40 eligible annotations. Each pass groups at most six sources into a linked synthesis; original files remain intact. Use `planejar_compactacao` and `compactar_memoria` to review groups or supply an agent-written summary with checked source revisions. Changed sources invalidate the summary's search priority. This reduces repeated context, not physical file count. See [memory consolidation](docs/memory-consolidation.md).

Task context now accepts explicit `foco` files and an `objetivo`, returns active decisions and unchecked tasks, and explains budget omissions. `renomear_nota` previews Markdown moves, then applies a checked plan with wikilink/relative-link updates and a stable note identifier. See [task context and renaming](docs/task-context-and-renaming.md) for scope and recovery limits.
