# Per-user Windows installation. AtlasBrain runs in WSL; clients use localhost HTTP.
[CmdletBinding()]
param(
    [string]$Vault,
    [string]$Clients = 'codex,claude-code',
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$Distribution = 'Ubuntu',
    [switch]$UninstallStartup
)
$ErrorActionPreference = 'Stop'
$taskName = 'AtlasBrain'
function Invoke-Linux([string[]]$Arguments) {
    # Transport shell-quoted arguments through stdin. PowerShell 5.1 otherwise
    # strips embedded quotes from JSON and paths passed to native programs.
    $quoted = $Arguments | ForEach-Object { "'" + $_.Replace("'", "'\''") + "'" }
    $script = 'exec ' + ($quoted -join ' ') + ' #'
    $previousEncoding = $OutputEncoding
    try {
        $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $output = $script | & wsl.exe --distribution $Distribution --exec bash
    } finally { $OutputEncoding = $previousEncoding }
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed with exit code $LASTEXITCODE" }
    return $output
}
if ($UninstallStartup) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $linuxHome = (Invoke-Linux -Arguments @('printenv', 'HOME') | Select-Object -Last 1).Trim()
        $checkout = Split-Path -Parent $PSScriptRoot
        $linuxCheckout = (Invoke-Linux -Arguments @('wslpath', '-a', '-u', $checkout) | Select-Object -Last 1).Trim()
        $candidates = @("$linuxCheckout/.venv-wsl/bin/python", "$linuxHome/.local/share/atlasbrain/app/.venv-wsl/bin/python", "$linuxHome/.local/share/atlasbrain/app/.venv/bin/python")
        foreach ($candidate in $candidates) {
            $exists = Invoke-Linux -Arguments @('sh', '-c', 'if [ -x "$1" ]; then echo exists; fi', 'sh', $candidate)
            if ($exists -contains 'exists') {
                Invoke-Linux -Arguments @($candidate, '-m', 'atlasbrain.cli', 'stop') | Out-Host
                break
            }
        }
    }
    Write-Host 'Automatic startup removed. Project data and client settings are preserved.'
    return
}
if ($Distribution -notmatch '^[A-Za-z0-9._-]+$') { throw 'Unsupported distribution name.' }
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'Install WSL with wsl --install, restart Windows, then rerun this installer.' }
$distributions = @(& wsl.exe --list --quiet) | ForEach-Object { ($_ -replace "`0", '').Trim() }
if ($LASTEXITCODE -ne 0) { throw 'Cannot list WSL distributions. Finish WSL setup before running this installer.' }
if ($distributions -notcontains $Distribution) {
    & wsl.exe --install --distribution $Distribution
    throw 'Finish the WSL installation and create your Linux user (restart if requested), then rerun this installer.'
}
# All user paths travel as arguments, never interpolated shell source.
$clientHome = (Invoke-Linux -Arguments @('wslpath', '-a', '-u', $env:USERPROFILE) | Select-Object -Last 1).Trim()
$linuxHome = (Invoke-Linux -Arguments @('printenv', 'HOME') | Select-Object -Last 1).Trim()
if ($Vault) {
    $project = (Resolve-Path -LiteralPath $Vault).Path
    if (-not (Test-Path -LiteralPath $project -PathType Container)) { throw 'Vault must be a folder.' }
    $linuxVault = (Invoke-Linux -Arguments @('wslpath', '-a', '-u', $project) | Select-Object -Last 1).Trim()
} else {
    $linuxVault = "$linuxHome/AtlasBrain"
    Invoke-Linux -Arguments @('mkdir', '-p', $linuxVault) | Out-Null
}
$linuxRoot = "$linuxHome/.local/share/atlasbrain/app"
$checkout = Split-Path -Parent $PSScriptRoot
$localBootstrap = Join-Path $PSScriptRoot 'install.sh'
$hasCheckout = (Test-Path -LiteralPath (Join-Path $checkout 'atlasbrain/desktop_install.py')) -and (Test-Path -LiteralPath (Join-Path $checkout 'pyproject.toml'))
if ($hasCheckout) {
    $linuxRoot = (Invoke-Linux -Arguments @('wslpath', '-a', '-u', $checkout) | Select-Object -Last 1).Trim()
}
# A Windows Python environment cannot be reused by Linux.
$environment = "$linuxRoot/.venv-wsl"
$python = "$environment/bin/python"
$packagesReady = Invoke-Linux -Arguments @('sh', '-c', 'if command -v git >/dev/null && command -v curl >/dev/null; then echo ready; fi')
if ($packagesReady -notcontains 'ready') {
    # Keep stdin interactive for sudo's password prompt.
    & wsl.exe --distribution $Distribution --exec sudo apt-get update
    if ($LASTEXITCODE -ne 0) { throw 'Failed to update Ubuntu packages.' }
    & wsl.exe --distribution $Distribution --exec sudo apt-get install -y git curl ca-certificates
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install Ubuntu dependencies.' }
}
$bootstrap = $null
if (-not $hasCheckout) { $bootstrap = (Invoke-Linux -Arguments @('mktemp') | Select-Object -Last 1).Trim() }
try {
    if ($hasCheckout) {
        $bootstrap = (Invoke-Linux -Arguments @('wslpath', '-a', '-u', $localBootstrap) | Select-Object -Last 1).Trim()
    } else {
        Invoke-Linux -Arguments @('curl', '--fail', '--location', '--proto', '=https', '--tlsv1.2', 'https://raw.githubusercontent.com/danilofacco/atlasbrain/main/scripts/install.sh', '-o', $bootstrap) | Out-Host
    }
    $arguments = @('env', "UV_PROJECT_ENVIRONMENT=$environment", 'bash', $bootstrap, '--vault', $linuxVault, '--clients', $Clients, '--port', "$Port", '--platform', 'wsl', '--prepare-only', '--client-home', $clientHome)
    if ($Clients.Split(',') -contains 'claude-desktop-bridge') {
        $desktopFile = Join-Path $env:APPDATA 'Claude/claude_desktop_config.json'
        $linuxDesktop = (Invoke-Linux -Arguments @('wslpath', '-a', '-u', $desktopFile) | Select-Object -Last 1).Trim()
        $bridge = ConvertTo-Json -Compress -InputObject @("$env:SystemRoot\System32\wsl.exe", '--distribution', $Distribution, '--exec', $python, '-m', 'atlasbrain.http_bridge')
        $arguments += @('--desktop-config', $linuxDesktop, '--bridge-command-json', $bridge)
    }
    Invoke-Linux -Arguments $arguments | Out-Host
} finally {
    if (-not $hasCheckout) { Invoke-Linux -Arguments @('rm', '-f', $bootstrap) | Out-Null }
}
# Stop the previous managed instance before registering the foreground task.
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Invoke-Linux -Arguments @($python, '-m', 'atlasbrain.cli', 'stop') | Out-Host
$runner = "$linuxHome/.config/atlasbrain/run-service.sh"
# Distribution names are restricted here because Task Scheduler stores a command line.
if ($Distribution -notmatch '^[A-Za-z0-9._-]+$') { throw 'Use a distribution name containing letters, digits, dots, underscores or hyphens.' }
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wsl.exe" -Argument "--distribution $Distribution --exec sh `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($health.service -eq 'atlasbrain' -and $health.protocol -eq 1) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw "AtlasBrain did not become reachable from Windows. Check WSL localhost forwarding and $linuxHome/.config/atlasbrain/server.log." }
Invoke-Linux -Arguments @($python, '-m', 'atlasbrain.desktop_install', '--verify-url', "http://127.0.0.1:$Port/mcp") | Out-Host
Write-Host "Installed. AtlasBrain starts at login and is available at http://127.0.0.1:$Port. Reopen your clients and approve the global MCP when prompted."

# Windows resolves the real Desktop folder, including OneDrive redirection.
$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders.Item('Desktop')
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'AtlasBrain.url'))
$shortcut.TargetPath = "http://127.0.0.1:$Port/?v=$([Uri]::EscapeDataString($linuxVault))"
$shortcut.Save()
Write-Host 'AtlasBrain shortcut created on your Desktop. The interface shows the global MCP URL under MCP HTTP.'
