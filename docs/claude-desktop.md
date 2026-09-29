# Claude Desktop chat

Claude Code can connect directly to AtlasBrain's local HTTP endpoint. Claude Desktop's local MCP configuration launches stdio commands, so its chat needs a small stdio-to-HTTP bridge. The bridge connects to the existing AtlasBrain service; it does not launch another AtlasBrain server or use a second AtlasBrain port. Claude's cloud custom connectors cannot reach `127.0.0.1` on your computer. See the [Claude Desktop local MCP guide](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop) and [remote connector network requirements](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

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
