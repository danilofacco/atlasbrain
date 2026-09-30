# Codex CLI

Codex CLI connects directly to the existing AtlasBrain HTTP service. See the [official Codex MCP documentation](https://developers.openai.com/codex/mcp/).

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
   uv run atlasbrain setup --vault /absolute/path/to/my-project --client codex
   ```

2. Copy the URL from the output and register it using your installed Codex CLI:

   ```bash
   codex mcp add atlasbrain --url http://127.0.0.1:8765/projects/PROJECT_ID/mcp
   codex mcp list
   ```

   Replace the example URL first. Alternatively, merge setup's TOML into `~/.codex/config.toml` or `.codex/config.toml` in a trusted target project:

   ```toml
   [mcp_servers."atlasbrain"]
   url = "http://127.0.0.1:8765/projects/PROJECT_ID/mcp"
   ```

   Choose one registration method. Global settings are shared with [Codex Desktop](codex-desktop.md) on the same host.

3. Start a new Codex session in your target folder and use `/mcp` to inspect connected servers:

   ```bash
   cd /absolute/path/to/my-project
   codex
   ```

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
