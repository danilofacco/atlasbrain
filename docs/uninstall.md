# Uninstall AtlasBrain on macOS and Windows

Run these commands from the AtlasBrain checkout **before deleting or moving it**. Removing automatic startup is reversible: run the installation command again to enable it.

## 1. Disable automatic startup and stop AtlasBrain

### macOS

```bash
bash scripts/uninstall-startup.sh
.venv/bin/python -m atlasbrain.cli stop
```

The first command unloads the per-user LaunchAgent and removes `~/Library/LaunchAgents/com.atlasbrain.service.plist`. The second also stops an instance started manually. Disable the LaunchAgent first so it cannot restart the service.

If you used a custom installation checkout, use its `.venv/bin/python`. If you set `ATLASBRAIN_SERVICE_DIR`, supply the same environment variable when stopping that service.

### Windows (PowerShell)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UninstallStartup
```

For another installed distribution, use its original name:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UninstallStartup -Distribution Ubuntu-24.04
```

This stops and unregisters the `AtlasBrain` scheduled task, then attempts to stop the Linux service using the existing Python installation. Keep the checkout and its `.venv-wsl` available until this finishes. If WSL is unavailable or the environment has already been deleted, the task can be removed but the Linux stop step may fail; finish stopping the service before deleting its remaining files. WSL and its Ubuntu distribution are preserved.

## 2. Remove the MCP connection from your clients

Remove only the `atlasbrain` server entry; keep unrelated servers and application settings. Reopen the clients afterward. For Claude Desktop's HTTP connection, remove the AtlasBrain connector in the application's Connectors settings.

| Client | macOS configuration | Windows configuration |
| --- | --- | --- |
| Codex Desktop / CLI | `~/.codex/config.toml`: remove `[mcp_servers.atlasbrain]` or `[mcp_servers."atlasbrain"]` and any child tables | `%USERPROFILE%\.codex\config.toml`, same entry |
| Claude Code | `~/.claude.json`: remove `mcpServers.atlasbrain` | `%USERPROFILE%\.claude.json`, same entry |
| Antigravity | `~/.gemini/config/mcp_config.json`: remove `mcpServers.atlasbrain` | `%USERPROFILE%\.gemini\config\mcp_config.json`, same entry |
| OpenCode | `~/.config/opencode/opencode.json`: remove `mcp.atlasbrain` | `%USERPROFILE%\.config\opencode\opencode.json`, same entry |

If you chose another MCP server name, remove that name instead. Also remove any older AtlasBrain entries you added manually in project-scoped configurations. For an optional Claude Desktop stdio bridge, remove only its entry from `claude_desktop_config.json`. Preserve valid TOML/JSON syntax after editing.

## 3. Remove the shortcut and installation files

- Remove the **AtlasBrain** shortcut from your Desktop (`AtlasBrain.webloc` on macOS, `AtlasBrain.url` on Windows). Windows may redirect the Desktop to OneDrive.
- Delete the AtlasBrain installation checkout if it is no longer needed. Check for uncommitted work and any notes stored in that folder first. Its `.venv` or `.venv-wsl` dependencies are removed with it.
- The standalone downloaded bootstrap installs its checkout in `~/.local/share/atlasbrain/app`; on Windows this path is inside WSL. A cloned checkout remains where you cloned it.
- Optionally remove `~/.config/atlasbrain/` after stopping the service. It contains the project registry, logs, and startup runner; on Windows this directory is inside WSL. Use your custom `ATLASBRAIN_SERVICE_DIR` if applicable. Removing service state does not delete project notes.

The uninstall commands preserve every project's `.atlasbrain/`, the default notes folder (`~/AtlasBrain` or the older `~/SegundoCerebro`), and configuration backups (`*.backup-*`). **Keep these folders if you want to retain decisions, learnings, imported documents, summaries, indexes, and note history.** Delete them only if you deliberately want to remove that memory, after backing up what you need. Default notes folders are inside WSL on Windows.

Python, uv, Git, WSL, Ubuntu, and shared embedding caches can be used by other applications; removing AtlasBrain does not require deleting them.

## 4. Verify removal

On macOS, `launchctl print "gui/$(id -u)/com.atlasbrain.service"` should report that the service cannot be found. On Windows, `Get-ScheduledTask -TaskName AtlasBrain -ErrorAction SilentlyContinue` should return no task.

The AtlasBrain interface and `/health` endpoint at `http://127.0.0.1:8765` should no longer be reachable. Use the port you selected during installation. If the endpoint still identifies AtlasBrain, another running instance remains and should be stopped before removing installation files.
