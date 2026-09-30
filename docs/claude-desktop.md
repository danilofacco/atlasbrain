# Claude Desktop

For dependency installation and automatic startup after login, see the [automated installation guide](automatic-installation.md).

## Connect by HTTP URL

Use the [automated installer](automatic-installation.md) with `--clients codex,claude-code,claude-desktop` (or `-Clients` on Windows). It installs the shared background service and prints a project URL such as `http://127.0.0.1:8765/projects/PROJECT_ID/mcp`. In your Desktop version's Connectors settings, add that URL, then verify a `find_files` call. The installer does not write a binary-launch entry for this mode.

Check where your connector makes requests: the [published custom connector documentation](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) describes cloud-originating connections, which cannot reach your computer's localhost. Use the local connector route your Desktop installation supports; a background service alone does not make a local URL reachable from a cloud connector. AtlasBrain's installer verifies the local HTTP server, while Desktop connector support must be checked in the application.

## Optional local configuration bridge

For Desktop installations using command-based `claude_desktop_config.json`, use the installer's explicit `claude-desktop-bridge` client, or configure the existing bridge below. This client forwards requests to the same HTTP service; it does not create a second AtlasBrain instance. Claude Code connects over HTTP directly. See the [local MCP guide](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).

1. Start or reuse AtlasBrain and copy the URL printed by `setup`:

   ```bash
   uv run atlasbrain setup --vault /absolute/path/to/my-project --client claude
   ```

2. Install Node.js if `npx` is unavailable. Find its absolute path with `which npx`. In `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, merge this entry into the existing `mcpServers` object. Replace the command path and URL with yours:

   ```json
   {
     "mcpServers": {
       "atlasbrain": {
         "command": "/absolute/path/to/npx",
         "args": [
           "-y",
           "mcp-remote@0.14.3",
           "http://127.0.0.1:8765/projects/PROJECT_ID/mcp",
           "--transport",
           "http-only",
           "--allow-http",
           "--silent"
         ]
       }
     }
   }
   ```

   The [mcp-remote bridge](https://github.com/punkpeye/mcp-remote) runs as a lightweight client-side process while Claude Desktop is open. AtlasBrain remains one shared Python service on one port. The bridge needs npm access once to download the pinned package.

3. Fully quit and reopen Claude Desktop. Check **Settings → Developer** or the chat's **+ → Connectors** menu for AtlasBrain. After a computer restart, run `uv run atlasbrain start --vault /absolute/path/to/my-project` if the service is not running.

The Claude Desktop chat entry points to one specific project. Claude Code can instead use a local-scoped `atlasbrain` entry for each project, so it automatically selects the correct project while you work there.

For one fixed project, the URL above needs no extra tool arguments. If you connect Claude Desktop to the global brain instead, AtlasBrain exposes `projects`; pass `pasta_projeto` with the project folder on each project tool call. AtlasBrain will ask for a folder when it is missing, avoiding results from the wrong project. `isolated_files` without a folder lists isolated files across registered projects.
