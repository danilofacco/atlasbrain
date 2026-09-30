# Claude Code CLI

Claude Code connects directly to AtlasBrain over HTTP. Claude Desktop chat has a different configuration; follow the [Desktop bridge guide](claude-desktop.md) for that surface. See the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Before you start

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) on macOS or Linux, and install/sign in to your assistant. On Windows, run AtlasBrain in WSL and make sure the client can reach its localhost port. Native Windows service execution is currently unsupported.

Clone AtlasBrain once, then run the AtlasBrain commands below from this checkout:

```bash
git clone https://github.com/danilofacco/atlasbrain.git
cd atlasbrain
```

Replace `/absolute/path/to/my-project` with the folder you want to index. `uv` installs Python 3.12 and the dependencies automatically. Setup creates `.atlasbrain/`, starts or reuses the shared service, and prints configuration; it does not edit your assistant's settings. The first index downloads the embedding model and continues in the background.

## Connect AtlasBrain

1. Generate the configuration:

   ```bash
   uv run atlasbrain setup --vault /absolute/path/to/my-project --client claude
   ```

2. Copy the printed URL, then register it from the target project:

   ```bash
   cd /absolute/path/to/my-project
   claude mcp add --transport http --scope local atlasbrain http://127.0.0.1:8765/projects/PROJECT_ID/mcp
   claude mcp list
   ```

   Replace the example URL first. Local scope stores this entry for the current project in `~/.claude.json`, so the same server name can point at different projects in different folders.

   Alternatively, merge setup's JSON into `.mcp.json` at the target project's root:

   ```json
   {
     "mcpServers": {
       "atlasbrain": {
         "type": "http",
         "url": "http://127.0.0.1:8765/projects/PROJECT_ID/mcp"
       }
     }
   }
   ```

   Choose one method. `.mcp.json` uses project scope; Claude Code asks you to approve project MCP servers before using them. A local project ID is machine-specific, so teammates should generate their own URL.

3. Start a new `claude` session in the target folder and run `/mcp` to check AtlasBrain.

## Verify and use

Ask the assistant: “Use AtlasBrain to find the main files in this project and explain their dependencies.” It should call tools such as `find_files` and `explain`. The project-specific URL binds those calls to the chosen folder.

Open [the local interface](http://127.0.0.1:8765) to inspect indexing and the graph. After restarting your computer, run from the AtlasBrain checkout:

```bash
uv run atlasbrain start --vault /absolute/path/to/my-project
uv run atlasbrain status
```

If tools are missing, check that the service is running, copy the exact URL printed by setup (including its project ID and port), and reload the client. `127.0.0.1` refers to the machine running the client: a cloud session or another host cannot reach this service through that address.

To connect a second project, run setup with its folder and use a distinct server name with `--nome my-project`. Each entry stays bound to its project; all clients share the same AtlasBrain service and port. A global configuration pointing at one project remains bound to that project even when you open another folder.

See the [operation guide](features-and-operation.md) for service controls and optional hooks, and the [installation overview](../README.md#get-started) for other clients.
