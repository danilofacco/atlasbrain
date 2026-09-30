# Automatic desktop service installation

AtlasBrain is installed once per computer. A single HTTP service starts when the user logs in, independently of Codex, Claude or OpenCode. Each project has its own MCP URL, while sharing the process and port. Closing an editor does not stop AtlasBrain. This installs a background service, not a tray application or native window.

## macOS

From an AtlasBrain checkout:

```bash
bash scripts/install.sh --vault "/absolute/path/to/my-project"
```

Once these scripts are published on the repository's main branch, a new computer can download the bootstrap without cloning manually:

```bash
curl -fL https://raw.githubusercontent.com/danilofacco/atlasbrain/main/scripts/install.sh -o /tmp/atlasbrain-install.sh
bash /tmp/atlasbrain-install.sh --vault "/absolute/path/to/my-project"
```

The installer obtains uv if absent, clones AtlasBrain into `~/.local/share/atlasbrain/app`, installs managed Python 3.12 and locked dependencies, registers the project, configures Codex and Claude Code, and starts a per-user LaunchAgent. It uses absolute executable paths, so startup does not depend on your terminal's PATH or virtual environment activation. If Apple's Git tools are missing, macOS opens their installer; finish that OS installation and rerun the command.

The LaunchAgent runs at login and restarts a failed service. Logs are in `~/.config/atlasbrain/server.log`. The embedding model downloads during the first index; tools can connect while indexing continues.

For contributors testing an existing checkout, set `ATLASBRAIN_INSTALL_DIR` to that checkout before running the script. Existing installations are reused without deleting changes or switching branches.

## Windows

From PowerShell in the checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Vault "C:\Users\me\Projects\my-project"
```

After publication, a new computer can download the bootstrap:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/danilofacco/atlasbrain/main/scripts/install.ps1 -OutFile "$env:TEMP\atlasbrain-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\atlasbrain-install.ps1" -Vault "C:\Users\me\Projects\my-project"
```

AtlasBrain runs inside Ubuntu WSL, with Windows applications connecting to `http://127.0.0.1:8765`. A per-user Task Scheduler task named `AtlasBrain` launches WSL at login, runs the server in the foreground, stays active while it runs, and retries failures. It runs on battery as well. No administrator rights are required for the startup task.

If WSL itself is unavailable, run `wsl --install` from an administrator terminal, restart Windows if requested, and complete Ubuntu's initial Linux user setup. If Ubuntu is absent, the script requests its installation. Those OS permissions, any restart and the initial user creation cannot be skipped by a normal user installer. Rerun the script afterward. Git, curl, Python and application dependencies are then installed automatically; Linux may request your sudo password for system packages.

Use `-Distribution Ubuntu-24.04` for another installed Ubuntu distribution. The target project stays in its Windows folder, accessed through WSL's `/mnt/...` path. Client configurations are written into that same folder. Existing matching Codex global entries are migrated using the Windows user profile. A project ID is based on the WSL path and should not be copied between computers.

The installer checks the health endpoint from Windows, not just from Linux. WSL must allow localhost forwarding ([Microsoft networking documentation](https://learn.microsoft.com/en-us/windows/wsl/networking)). A conflicting port or unreachable service produces an error instead of a success message.

## Clients

Select clients with `--clients` on macOS or `-Clients` on Windows. Defaults are `codex,claude-code`. Available choices:

| Client | Installation result |
| --- | --- |
| `codex` | HTTP URL in the project's `.codex/config.toml`; migrate an existing same-name global server to HTTP. Used by Desktop and CLI. |
| `claude-code` | HTTP entry in the project's `.mcp.json`. Also relevant to Claude Desktop Code sessions using that project configuration. |
| `antigravity` | HTTP `serverUrl` in the project's `.agents/mcp_config.json`, supported by IDE and CLI. |
| `opencode` | HTTP entry in the project's `opencode.json`. Existing JSONC configuration requires a manual merge. |
| `claude-desktop` | Print the local HTTP URL for adding through Desktop's Connectors UI. No stdio command is written. |
| `claude-desktop-bridge` | Optional compatibility route for installations using `claude_desktop_config.json`: a small client forwards tools to the shared HTTP server using the already installed Python. It never starts an AtlasBrain service. |

Example:

```bash
bash scripts/install.sh --vault "/path/to/project" --clients codex,claude-code,claude-desktop,opencode
```

Desktop connectors are account/application settings: the installer provides the URL but does not bypass that application's connector installation or consent UI. Validate the local URL in your installed Desktop version. Anthropic's published documentation describes custom connectors that originate in its cloud, which cannot reach your localhost; these should not be confused with a client that actually connects locally. A cloud-originating connector would require a separately configured reachable endpoint and authentication, not merely a background service ([Anthropic connector documentation](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).

Reopen your clients after installation. Codex must trust the project before loading project settings; Claude Code may ask you to approve its project MCP. The installer preserves unrelated servers and backs up changed settings alongside the original file as `*.backup-*`. It does not bypass trust prompts. Entries with the same server name in a higher-priority scope can override project settings; remove obsolete client-specific overrides if necessary.

To add a second project, rerun the installer with its folder. Registered brains remain available and the service indexes them all. Global Desktop connectors stay bound to the project in their URL: register separate connectors for different projects. Use `--port`/`-Port` to choose a free port for the initial installation. Existing live services must keep their current port.

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

macOS, from the checkout:

```bash
bash scripts/uninstall-startup.sh
```

Windows:

```powershell
.\scripts\install.ps1 -UninstallStartup
```

This stops/removes the startup supervisor. It preserves installation files, client settings and every project's `.atlasbrain/` data. Reinstall to enable it again. While managed startup is enabled, `atlasbrain stop` may cause the supervisor to start it again; disable the supervisor to stop it permanently.
