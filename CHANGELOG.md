# Changelog

## 0.2.2 — 2026-10-03

- Disable automatic transcript extraction by default to prevent background curator sessions from cluttering assistant history. Existing capture hooks and queued workers remain inactive unless explicitly enabled.
- Keep project memory available through MCP in the current conversation. Normal hook installation preserves memory reminders and removes legacy completion capture handlers without affecting other tools' hooks.
- Require `atlasbrain hooks --capture` to install optional extraction hooks. Request no session persistence for Claude extraction, as already done for Codex.
- Synchronize the Python package version with the release metadata.

Installed Git clones can update with `atlasbrain update apply`. Automatic updates also detect this release when enabled and the checkout is clean. Run `atlasbrain hooks` after updating to remove obsolete capture handlers from client settings; legacy handlers are already inactive by default with this release.
