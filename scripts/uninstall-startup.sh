#!/usr/bin/env bash
set -euo pipefail
[[ $(uname -s) == Darwin ]] || { echo 'On Windows use install.ps1 -UninstallStartup.' >&2; exit 2; }
launchctl bootout "gui/$(id -u)/com.atlasbrain.service" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.atlasbrain.service.plist"
echo 'Automatic startup removed. Project data and client configurations are preserved.'
