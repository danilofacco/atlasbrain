# Automatic desktop service installation

AtlasBrain is installed once per computer. A single HTTP service starts when the user logs in, independently of Codex, Claude or OpenCode. All projects use one global MCP URL: `http://127.0.0.1:8765/mcp`. Closing an editor does not stop AtlasBrain. Installation also creates an AtlasBrain shortcut on the Desktop. It opens the selected project in your default browser; closing that window does not stop the service. On macOS the shortcut is a `.webloc`; Windows uses a `.url` and resolves your actual Desktop folder, including OneDrive. This installs a background service and browser launcher, not a tray application or native window.

In the interface, open **MCP HTTP** to see and copy the global URL. Switching projects does not change that URL, and the address uses the same host and port as the interface, including a custom installation port.

## macOS

From an AtlasBrain checkout (no project argument is required):

```bash
bash scripts/install.sh
```

Once these scripts are published on the repository's main branch, a new computer can download the bootstrap without cloning manually:

```bash
curl -fL https://raw.githubusercontent.com/danilofacco/atlasbrain/main/scripts/install.sh -o /tmp/atlasbrain-install.sh
bash /tmp/atlasbrain-install.sh
```

The installer obtains uv if absent, uses the checkout containing the script (the standalone downloaded bootstrap clones into `~/.local/share/atlasbrain/app`), installs managed Python 3.12 and locked runtime dependencies, registers the project, configures Codex and Claude Code, and starts a per-user LaunchAgent. It uses absolute executable paths, so startup does not depend on your terminal's PATH or virtual environment activation. If Apple's Git tools are missing, macOS opens their installer; finish that OS installation and rerun the command.

The LaunchAgent runs at login and restarts a failed service. Logs are in `~/.config/atlasbrain/server.log`. The embedding model downloads during the first index; tools can connect while indexing continues.

The checkout must remain at its installation path: the startup service uses it directly. Run the command again after moving it. `ATLASBRAIN_INSTALL_DIR` optionally selects another checkout. Existing installations are reused without deleting changes or switching branches.

## Windows

From PowerShell in the checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

After publication, a new computer can download the bootstrap:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/danilofacco/atlasbrain/main/scripts/install.ps1 -OutFile "$env:TEMP\atlasbrain-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\atlasbrain-install.ps1"
```

The Windows installer uses the cloned checkout directly, with a separate `.venv-wsl` Linux environment; it does not download a different repository version. Keep the checkout at its installation path, or rerun the installer after moving it. AtlasBrain runs inside Ubuntu WSL, with Windows applications connecting to `http://127.0.0.1:8765`. A per-user Task Scheduler task named `AtlasBrain` launches WSL at login, runs the server in the foreground, stays active while it runs, and retries failures. It runs on battery as well. No administrator rights are required for the startup task.

If WSL itself is unavailable, run `wsl --install` from an administrator terminal, restart Windows if requested, and complete Ubuntu's initial Linux user setup. If Ubuntu is absent, the script requests its installation. Those OS permissions, any restart and the initial user creation cannot be skipped by a normal user installer. Rerun the script afterward. Git, curl, Python and application dependencies are then installed automatically; Linux may request your sudo password for system packages.

Use `-Distribution Ubuntu-24.04` for another installed Ubuntu distribution. The target project stays in its Windows folder, accessed through WSL's `/mnt/...` path. Client configurations are written globally using the Windows user profile. Windows workspace file URIs are translated to WSL `/mnt/<drive>/...` paths.

The installer checks the health endpoint from Windows, not just from Linux. WSL must allow localhost forwarding ([Microsoft networking documentation](https://learn.microsoft.com/en-us/windows/wsl/networking)). A conflicting port or unreachable service produces an error instead of a success message. Both installers also verify an MCP initialization handshake and discovery of the global tools before reporting success.

`--vault` on macOS or `-Vault` on Windows optionally registers an initial folder for the interface; it does not bind the MCP connection to that folder.

## Clients

Select clients with `--clients` on macOS or `-Clients` on Windows. Defaults are `codex,claude-code`. Available choices:

| Client | Installation result |
| --- | --- |
| `codex` | HTTP URL in `~/.codex/config.toml`; migrate an existing same-name server to HTTP. Used by Desktop and CLI. |
| `claude-code` | HTTP entry in `~/.claude.json`, at user scope. |
| `antigravity` | HTTP `serverUrl` in `~/.gemini/config/mcp_config.json`, supported by IDE and CLI. |
| `opencode` | HTTP entry in `~/.config/opencode/opencode.json`. Existing JSONC configuration requires a manual merge. |
| `claude-desktop` | Print the global local HTTP URL for adding through Desktop's Connectors UI. No stdio command is written. |
| `claude-desktop-bridge` | Optional compatibility route for installations using `claude_desktop_config.json`: a small client forwards tools to the shared HTTP server using the already installed Python. It never starts an AtlasBrain service. |

Example:

```bash
bash scripts/install.sh --vault "/path/to/project" --clients codex,claude-code,claude-desktop,opencode
```

Desktop connectors are account/application settings: the installer provides the URL but does not bypass that application's connector installation or consent UI. Validate the local URL in your installed Desktop version. Anthropic's published documentation describes custom connectors that originate in its cloud, which cannot reach your localhost; these should not be confused with a client that actually connects locally. A cloud-originating connector would require a separately configured reachable endpoint and authentication, not merely a background service ([Anthropic connector documentation](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).

Reopen your clients after installation to reload the global MCP configuration. The installer migrates obsolete same-name project entries for registered projects and preserves unrelated settings. The installer preserves unrelated servers and backs up changed settings alongside the original file as `*.backup-*`. It does not bypass trust prompts. Entries with the same server name in a higher-priority scope can override project settings; remove obsolete client-specific overrides if necessary.

No additional MCP entry is needed for another project. A single unambiguous local workspace root supplied by the client selects and registers the project automatically. For clients without roots, the assistant supplies `project_folder` from its working conversation context. With multiple roots, the assistant selects the relevant folder explicitly. Missing context is rejected rather than guessed. The browser selection and the daemon working directory never select the MCP project. Registered brains are indexed by the shared service. Use `--port`/`-Port` to choose a free port for the initial installation. Existing live services must keep their current port.

## Verify after login

Open `http://127.0.0.1:8765/health`: it should identify `atlasbrain` with protocol `1`. Open the interface at `http://127.0.0.1:8765`. Ask an assistant to call `find_files` for your project. Then log out and back in, or restart the computer, and repeat without opening a terminal to start the server.

On macOS, inspect the LaunchAgent:

```bash
launchctl print "gui/$(id -u)/com.atlasbrain.service"
```

On Windows:

```powershell
Get-ScheduledTask -TaskName AtlasBrain
Invoke-RestMethod http://127.0.0.1:8765/health
```

## Remove automatic startup

For complete removal, including client entries and optional cleanup, follow the [macOS and Windows uninstall guide](uninstall.md).

macOS, from the checkout:

```bash
bash scripts/uninstall-startup.sh
```

Windows:

```powershell
.\scripts\install.ps1 -UninstallStartup
```

This stops/removes the startup supervisor. It preserves installation files, client settings and every project's `.atlasbrain/` data. Reinstall to enable it again. While managed startup is enabled, `atlasbrain stop` may cause the supervisor to start it again; disable the supervisor to stop it permanently.
