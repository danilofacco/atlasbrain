# Per-user native Windows installation; no Linux runtime is required.
[CmdletBinding()]
param(
    [string]$Vault,
    [string]$Clients = 'codex,claude-code',
    [ValidateRange(1,65535)][int]$Port = 8765,
    [switch]$UninstallStartup
)
$ErrorActionPreference = 'Stop'
$taskName = 'AtlasBrain'
$env:PYTHONUTF8 = '1'
$checkout = Split-Path -Parent $PSScriptRoot
$hasCheckout = (Test-Path -LiteralPath (Join-Path $checkout 'atlasbrain/desktop_install.py')) -and (Test-Path -LiteralPath (Join-Path $checkout 'pyproject.toml'))
if ($env:ATLASBRAIN_INSTALL_DIR) { $checkout = $env:ATLASBRAIN_INSTALL_DIR }
elseif (-not $hasCheckout) { $checkout = Join-Path $env:LOCALAPPDATA 'AtlasBrain/app' }
$checkout = [IO.Path]::GetFullPath($checkout)
$environment = Join-Path $checkout '.venv-windows'
if ($env:UV_PROJECT_ENVIRONMENT) {
    $environment = $env:UV_PROJECT_ENVIRONMENT
    if (-not [IO.Path]::IsPathRooted($environment)) { $environment = Join-Path $checkout $environment }
}
$python = Join-Path $environment 'Scripts/python.exe'
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}
function Stop-Startup {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        # Stop legacy tasks before replacing them; native tasks shut down gracefully.
        if ($task.Actions.Execute -match 'wsl(\.exe)?$') {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $python) {
        Push-Location $checkout
        try { Invoke-Checked $python @('-X', 'utf8', '-m', 'atlasbrain.cli', 'stop') }
        finally { Pop-Location }
    }
    if ($task) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
}
if ($UninstallStartup) {
    Stop-Startup
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'Automatic startup removed. Project data and client settings are preserved.'
    return
}
$allowedClients = @('codex', 'claude-code', 'claude-desktop', 'claude-desktop-bridge', 'antigravity', 'opencode')
foreach ($client in $Clients.Split(',')) {
    if ($client -and $client -notin $allowedClients) { throw "Unknown client: $client" }
}
if ($Vault) {
    $project = (Resolve-Path -LiteralPath $Vault).Path
    if (-not (Test-Path -LiteralPath $project -PathType Container)) { throw 'Vault must be a folder.' }
} elseif ($env:ATLASBRAIN_GLOBAL) {
    $project = [IO.Path]::GetFullPath($env:ATLASBRAIN_GLOBAL)
    New-Item -ItemType Directory -Path $project -Force | Out-Null
} elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE 'SegundoCerebro')) {
    $project = Join-Path $env:USERPROFILE 'SegundoCerebro'
} else {
    $project = Join-Path $env:USERPROFILE 'AtlasBrain'
    New-Item -ItemType Directory -Path $project -Force | Out-Null
}
# Locate Git even when it was installed after this terminal was opened.
$git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $git) {
    foreach ($candidate in @((Join-Path $env:LOCALAPPDATA 'Programs/Git/cmd/git.exe'), (Join-Path $env:ProgramFiles 'Git/cmd/git.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $git = Get-Item -LiteralPath $candidate; break }
    }
}
if (-not $git) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { throw 'Install Git for Windows, then rerun this installer.' }
    Invoke-Checked 'winget.exe' @('install', '--id', 'Git.Git', '--exact', '--source', 'winget', '--scope', 'user', '--accept-package-agreements', '--accept-source-agreements')
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $git) {
        foreach ($candidate in @((Join-Path $env:LOCALAPPDATA 'Programs/Git/cmd/git.exe'), (Join-Path $env:ProgramFiles 'Git/cmd/git.exe'))) {
            if (Test-Path -LiteralPath $candidate) { $git = Get-Item -LiteralPath $candidate; break }
        }
    }
    if (-not $git) { throw 'Git installation finished; reopen PowerShell and rerun this installer.' }
}
$gitPath = if ($git.Source) { $git.Source } else { $git.FullName }
$env:PATH = (Split-Path -Parent $gitPath) + ';' + $env:PATH
$uv = Get-Command uv.exe -ErrorAction SilentlyContinue
$uvPath = if ($uv) { $uv.Source } else { Join-Path $env:USERPROFILE '.local/bin/uv.exe' }
if (-not (Test-Path -LiteralPath $uvPath)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $uvInstaller = Join-Path $env:TEMP (('atlasbrain-uv-' + [Guid]::NewGuid().ToString('N')) + '.ps1')
    try {
        Invoke-WebRequest -UseBasicParsing 'https://astral.sh/uv/install.ps1' -OutFile $uvInstaller
        Invoke-Checked 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $uvInstaller)
    } finally { Remove-Item -LiteralPath $uvInstaller -ErrorAction SilentlyContinue }
    $uv = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($uv) { $uvPath = $uv.Source }
    if (-not (Test-Path -LiteralPath $uvPath)) { throw 'Cannot find uv.exe after installation.' }
}
$env:PATH = (Split-Path -Parent $uvPath) + ';' + $env:PATH
if (-not (Test-Path -LiteralPath $checkout)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $checkout) -Force | Out-Null
    Invoke-Checked $gitPath @('clone', 'https://github.com/danilofacco/atlasbrain.git', $checkout)
} elseif (-not ((Test-Path -LiteralPath (Join-Path $checkout '.git')) -and (Test-Path -LiteralPath (Join-Path $checkout 'atlasbrain/desktop_install.py')))) {
    throw "Installation folder is not a compatible AtlasBrain checkout: $checkout"
}
$env:UV_PROJECT_ENVIRONMENT = $environment
Push-Location $checkout
try {
    Invoke-Checked $uvPath @('sync', '--locked', '--no-dev', '--python', '3.12')
    # --option=value preserves empty options in Windows PowerShell 5.1.
    Invoke-Checked $python @('-X', 'utf8', '-m', 'atlasbrain.desktop_install', "--vault=$project", "--clients=$Clients", "--port=$Port", '--platform=windows', '--prepare-only', "--client-home=$($env:USERPROFILE)")
    Stop-Startup
    $runner = Join-Path $env:USERPROFILE '.config/atlasbrain/service-launch.json'
    if ($env:ATLASBRAIN_SERVICE_DIR) { $runner = Join-Path $env:ATLASBRAIN_SERVICE_DIR 'service-launch.json' }
    $pythonw = Join-Path $environment 'Scripts/pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Managed Python is missing pythonw.exe.' }
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "-X utf8 -m atlasbrain.desktop_install --run-service `"$runner`"" -WorkingDirectory $checkout
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
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
    if (-not $ready) { throw "AtlasBrain startup failed. Check the server.log beside $runner." }
    Invoke-Checked $python @('-X', 'utf8', '-m', 'atlasbrain.desktop_install', "--verify-url=http://127.0.0.1:$Port/mcp", '--verify-owned')
} finally { Pop-Location }
$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders.Item('Desktop')
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'AtlasBrain.url'))
$shortcut.TargetPath = "http://127.0.0.1:$Port/?v=$([Uri]::EscapeDataString($project))"
$shortcut.Save()
Write-Host "Installed natively. AtlasBrain starts at login: http://127.0.0.1:$Port. Reopen your clients."
Write-Host 'AtlasBrain shortcut created on your Desktop. The global MCP URL is shown under MCP HTTP.'
