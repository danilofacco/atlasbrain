# Codex Desktop

Use the [automated installer](automatic-installation.md) to install dependencies, configure the client and start AtlasBrain at login. One global HTTP connection serves all projects. See the [official client documentation](https://developers.openai.com/codex/mcp/).

## Manual configuration

Codex uses `~/.codex/config.toml` for Desktop and CLI. Merge:

```toml
[mcp_servers.atlasbrain]
url = "http://127.0.0.1:8765/mcp"
```

Preserve unrelated servers. The installer backs up changed settings and migrates old AtlasBrain entries. If a project overrides the same server name, remove that obsolete entry. Use your installation's port if it differs from 8765. Reopen the client after changing settings.

## Automatic project selection

AtlasBrain uses a single local workspace root supplied by the client. Without roots support, its MCP instructions tell the assistant to supply `project_folder` from the current conversation's working folder automatically. Multiple roots require that folder selector as well. Missing context produces a request for a folder instead of selecting another project's data. New valid workspace folders are registered automatically. Switching the folder in the browser does not select the MCP project.

## Verify

Open [the interface](http://127.0.0.1:8765) and **MCP HTTP** to copy the global URL. Ask the assistant to find the main files in the current project, then repeat from another project using the same MCP entry. With automatic startup installed, no terminal command is needed after login.

For manual service installation, run `uv run --python 3.12 atlasbrain setup --vault /absolute/path/to/project --client codex` from the AtlasBrain checkout. This prints the same global URL and registers the initial folder. On Windows the service runs in WSL, while clients use the localhost HTTP address. Cloud sessions cannot reach your local machine via localhost.
