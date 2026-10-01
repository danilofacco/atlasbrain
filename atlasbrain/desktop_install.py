"""Install per-user startup and project client settings; never delete project data."""
import argparse
import asyncio
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import urlencode
from . import config, service

LABEL = 'com.atlasbrain.service'


def save(path: Path, content: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode() if isinstance(content, str) else content
    if path.exists():
        if path.read_bytes() == payload:
            return
        backup = path.with_name(path.name + f'.backup-{time.time_ns()}')
        backup.write_bytes(path.read_bytes())
    temporary = path.with_name(path.name + '.install-tmp')
    temporary.write_bytes(payload)
    temporary.replace(path)


def merge_json(path, section, name, entry):
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get(section, {}), dict):
        raise ValueError(f'Invalid configuration: {path}')
    data.setdefault(section, {})[name] = entry
    save(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def codex_config(path, name, url):
    original = path.read_text() if path.exists() else ''
    parsed = tomllib.loads(original)
    # Replace exactly this server, including child tables; preserve unrelated settings.
    lines = original.splitlines(keepends=True)
    kept, removing = [], False
    for line in lines:
        if re.match(r'^\s*\[', line):
            header = line.strip().split('#', 1)[0].strip()
            try:
                table = tomllib.loads(header + '\n__atlasbrain_marker__ = true\n')
                removing = name in table.get('mcp_servers', {})
            except tomllib.TOMLDecodeError:
                removing = False
        if not removing:
            kept.append(line)
    content = ''.join(kept).rstrip() + ('\n\n' + f'[mcp_servers.{json.dumps(name)}]\nurl = {json.dumps(url)}\n' if url else '\n')
    result = tomllib.loads(content)
    expected = dict(parsed.get('mcp_servers', {}))
    if url:
        expected[name] = {'url': url}
    else:
        expected.pop(name, None)
    if result.get('mcp_servers') != expected:
        raise ValueError(f'Unsupported inline MCP configuration in {path}; migrate it to tables first.')
    save(path, content.lstrip('\n'))


def configure_clients(vault, clients, name, port, desktop_config=None, bridge_command=None, client_home=None):
    url = f'http://127.0.0.1:{port}/mcp'
    client_home = client_home or Path.home()
    for client in clients:
        if client == 'codex':
            global_config = client_home / '.codex/config.toml'
            codex_config(global_config, name, url)
            scoped = vault / '.codex/config.toml'
            if scoped.exists() and name in tomllib.loads(scoped.read_text()).get('mcp_servers', {}):
                codex_config(scoped, name, None)
        elif client == 'claude-code':
            merge_json(client_home / '.claude.json', 'mcpServers', name, {'type': 'http', 'url': url})
        elif client == 'antigravity':
            merge_json(client_home / '.gemini/config/mcp_config.json', 'mcpServers', name, {'serverUrl': url})
        elif client == 'opencode':
            if (vault / 'opencode.jsonc').exists() or (client_home / '.config/opencode/opencode.jsonc').exists():
                raise ValueError('Existing opencode.jsonc: merge the HTTP entry manually to avoid shadowing it.')
            merge_json(client_home / '.config/opencode/opencode.json', 'mcp', name, {'type': 'remote', 'url': url, 'enabled': True})
        elif client == 'claude-desktop':
            # Connectors are registered in the application's account settings.
            # Emit the URL; do not write a stdio entry for a connector.
            continue
        elif client == 'claude-desktop-bridge':
            if desktop_config is None:
                if sys.platform != 'darwin':
                    raise ValueError('Supply --desktop-config for Claude Desktop outside macOS.')
                desktop_config = Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
            command = bridge_command or [sys.executable, '-m', 'atlasbrain.http_bridge']
            merge_json(desktop_config, 'mcpServers', name, {'command': command[0], 'args': command[1:] + [url]})
    # Remove only this server from old project scopes for the clients being configured.
    folders = {vault} | {Path(brain['path']) for brain in config.registered()}
    for folder in folders:
        scoped_codex = folder / '.codex/config.toml'
        if 'codex' in clients and scoped_codex.exists() and name in tomllib.loads(scoped_codex.read_text()).get('mcp_servers', {}):
            codex_config(scoped_codex, name, None)
        for client, scoped, section in (('claude-code', folder / '.mcp.json', 'mcpServers'), ('antigravity', folder / '.agents/mcp_config.json', 'mcpServers'), ('opencode', folder / 'opencode.json', 'mcp')):
            if client in clients and scoped.exists():
                data = json.loads(scoped.read_text())
                if name in data.get(section, {}):
                    del data[section][name]
                    save(scoped, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    if 'claude-code' in clients:
        path = client_home / '.claude.json'
        data = json.loads(path.read_text())
        changed = False
        for project in data.get('projects', {}).values():
            if isinstance(project, dict) and name in project.get('mcpServers', {}):
                del project['mcpServers'][name]
                changed = True
        if changed:
            save(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return url


def daemon_command(vault, port):
    return [sys.executable, '-m', 'atlasbrain.cli', '_daemon', '--vault', str(vault), '--port', str(port)]


def startup_files(vault, port, platform):
    directory = service.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    if platform == 'macos':
        path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
        environment = {key: os.environ[key] for key in ('ATLASBRAIN_SERVICE_DIR', 'ATLASBRAIN_GLOBAL', 'ATLASBRAIN_NO_EMBED') if key in os.environ}
        manifest = {'Label': LABEL, 'ProgramArguments': daemon_command(vault, port),
                    'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True,
                    'ThrottleInterval': 10, 'StandardOutPath': str(directory / 'server.log'),
                    'StandardErrorPath': str(directory / 'server.log'), 'EnvironmentVariables': environment}
        save(path, plistlib.dumps(manifest))
    else:
        path = directory / 'run-service.sh'
        environment = {key: os.environ[key] for key in ('ATLASBRAIN_SERVICE_DIR', 'ATLASBRAIN_GLOBAL', 'ATLASBRAIN_NO_EMBED') if key in os.environ}
        exports = ''.join(f'export {key}={shlex.quote(value)}\n' for key, value in environment.items())
        save(path, '#!/bin/sh\nset -eu\n' + exports + f'cd {shlex.quote(str(root))}\nexec ' + shlex.join(daemon_command(vault, port)) + ' >>' + shlex.quote(str(directory / 'server.log')) + ' 2>&1\n')
        path.chmod(0o700)
    return path


def desktop_shortcut(vault, port):
    url = f'http://127.0.0.1:{port}/?' + urlencode({'v': str(vault)})
    path = Path.home() / 'Desktop/AtlasBrain.webloc'
    save(path, plistlib.dumps({'URL': url}))
    return path


def activate_macos(path):
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{domain}/{LABEL}'], capture_output=True)
    service.stop()
    subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)


async def verify_mcp(url):
    """Verify the actual MCP handshake and tool discovery, beyond HTTP health."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not {'find_files', 'projects'} <= {tool.name for tool in tools.tools}:
                raise RuntimeError('The endpoint is not the current global AtlasBrain MCP.')


def check_mcp(url):
    asyncio.run(asyncio.wait_for(verify_mcp(url), timeout=20))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=config.GLOBAL_BRAIN)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--name', default='atlasbrain')
    parser.add_argument('--clients', default='codex,claude-code')
    parser.add_argument('--platform', choices=('macos', 'wsl'), default='macos' if sys.platform == 'darwin' else 'wsl')
    parser.add_argument('--desktop-config', type=Path)
    parser.add_argument('--client-home', type=Path)
    parser.add_argument('--bridge-command-json', help='Windows wsl.exe command as a JSON array')
    parser.add_argument('--verify-url', help='check an installed MCP endpoint without changing settings')
    parser.add_argument('--prepare-only', action='store_true', help='write startup files without activating them')
    args = parser.parse_args()
    if args.verify_url:
        check_mcp(args.verify_url)
        print("MCP handshake and global tool discovery verified.")
        return
    vault = args.vault.expanduser().resolve()
    if vault == config.GLOBAL_BRAIN.resolve():
        vault.mkdir(parents=True, exist_ok=True)
    if not vault.is_dir() or config.invalid_folder_reason(vault):
        parser.error('Choose an existing project or notes folder, not a system/home folder.')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    clients = args.clients.split(',') if args.clients else []
    if set(clients) - {'codex', 'claude-code', 'claude-desktop', 'claude-desktop-bridge', 'antigravity', 'opencode'}:
        parser.error('Clients: codex,claude-code,claude-desktop,claude-desktop-bridge,antigravity,opencode')
    live = service.owned_health()
    if live and live['port'] != args.port:
        parser.error(f"Existing service uses port {live['port']}; use that port.")
    if args.platform == 'wsl' and not args.prepare_only:
        parser.error('WSL startup must be activated by install.ps1; use --prepare-only.')
    config.data_dir(vault)
    bridge = json.loads(args.bridge_command_json) if args.bridge_command_json else None
    url = configure_clients(vault, clients, args.name, args.port, args.desktop_config, bridge, args.client_home)
    path = startup_files(vault, args.port, args.platform)
    shortcut = desktop_shortcut(vault, args.port) if args.platform == 'macos' else None
    if not args.prepare_only:
        if args.platform != 'macos':
            parser.error('WSL startup must be activated by install.ps1; use --prepare-only.')
        activate_macos(path)
        for _ in range(60):
            if service.health(args.port):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f'Startup failed. Check {service.state_dir() / "server.log"}')
        check_mcp(url)
    print(json.dumps({'url': url, 'startup': str(path), 'shortcut': str(shortcut) if shortcut else None, 'python': sys.executable, 'vault': str(vault), 'claude_desktop_connector': url if 'claude-desktop' in clients else None}))


if __name__ == '__main__':
    main()
