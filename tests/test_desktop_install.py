import json
import plistlib
import sys
import tomllib
from pathlib import Path
import pytest
from atlasbrain import desktop_install as install


def test_codex_replaces_stdio_and_preserves_other_servers(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('model = "example"\n[mcp_servers.atlasbrain]\ncommand = "old"\nargs = []\n[mcp_servers.atlasbrain.env]\nOLD = "yes"\n[mcp_servers.other]\ncommand = "keep"\n')
    install.codex_config(path, 'atlasbrain', 'http://localhost/mcp')
    data = tomllib.loads(path.read_text())
    assert data['model'] == 'example'
    assert data['mcp_servers'] == {'atlasbrain': {'url': 'http://localhost/mcp'}, 'other': {'command': 'keep'}}
    assert len(list(tmp_path.glob('*.backup-*'))) == 1
    install.codex_config(path, 'atlasbrain', 'http://localhost/mcp')
    assert len(list(tmp_path.glob('*.backup-*'))) == 1


def test_malformed_config_untouched(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[broken')
    with pytest.raises(tomllib.TOMLDecodeError):
        install.codex_config(path, 'atlasbrain', 'http://localhost/mcp')
    assert path.read_text() == '[broken'


def test_json_preserves_settings_and_other_servers(tmp_path):
    path = tmp_path / '.mcp.json'
    path.write_text('{"setting": true, "mcpServers": {"other": {"command": "keep"}}}')
    install.merge_json(path, 'mcpServers', 'atlasbrain', {'type': 'http', 'url': 'http://localhost/mcp'})
    data = json.loads(path.read_text())
    assert data['setting'] is True
    assert data['mcpServers']['other']['command'] == 'keep'


def test_project_configuration(tmp_path):
    url = install.configure_clients(tmp_path, ['codex', 'claude-code', 'antigravity', 'opencode'], 'atlasbrain', 9876)
    assert url == 'http://127.0.0.1:9876/mcp'
    assert json.loads((Path.home() / '.claude.json').read_text())['mcpServers']['atlasbrain']['url'] == url
    assert json.loads((Path.home() / '.config/opencode/opencode.json').read_text())['mcp']['atlasbrain']['type'] == 'remote'
    assert json.loads((Path.home() / '.gemini/config/mcp_config.json').read_text())['mcpServers']['atlasbrain']['serverUrl'] == url


def test_launchagent_paths_and_no_editor_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'state with spaces'))
    vault = tmp_path / 'project with spaces'
    manifest = plistlib.loads(install.startup_files(vault, 9876, 'macos').read_bytes())
    assert manifest['KeepAlive'] is True
    assert manifest['RunAtLoad'] is True
    assert manifest['ProgramArguments'] == [sys.executable, '-m', 'atlasbrain.cli', '_daemon', '--vault', str(vault), '--port', '9876']
    assert manifest['EnvironmentVariables']['ATLASBRAIN_SERVICE_DIR'].endswith('state with spaces')


def test_wsl_runner_escapes_paths(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / "state ' special"))
    path = install.startup_files(tmp_path / "project ' $(false)", 9876, 'wsl')
    subprocess.run(['sh', '-n', str(path)], check=True)
    assert 'exec ' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o700


def test_codex_global_stdio_migration(tmp_path):
    home = tmp_path / 'home'
    path = home / '.codex/config.toml'
    path.parent.mkdir(parents=True)
    path.write_text('[mcp_servers.atlasbrain]\ncommand = "old"\nargs = ["old"]\n')
    vault = tmp_path / 'project'
    url = install.configure_clients(vault, ['codex'], 'atlasbrain', 9876, client_home=home)
    assert tomllib.loads(path.read_text())['mcp_servers']['atlasbrain'] == {'url': url}


def test_desktop_connector_does_not_write_stdio_configuration(tmp_path):
    desktop = tmp_path / 'claude.json'
    install.configure_clients(tmp_path, ['claude-desktop'], 'atlasbrain', 9876, desktop_config=desktop)
    assert not desktop.exists()


def test_bootstrap_arguments_and_empty_optional_arrays(tmp_path):
    import os
    import subprocess
    root = tmp_path / 'app'
    (root / '.git').mkdir(parents=True)
    (root / 'atlasbrain').mkdir()
    (root / 'atlasbrain/desktop_install.py').touch()
    (root / '.venv/bin').mkdir(parents=True)
    executable = root / '.venv/bin/python'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    executable.chmod(0o700)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text('#!/bin/sh\nexit 0\n')
    uv.chmod(0o700)
    project = tmp_path / "project with ' spaces"
    project.mkdir()
    script = Path(__file__).resolve().parents[1] / 'scripts/install.sh'
    result = subprocess.run(['bash', str(script), '--vault', str(project), '--platform', 'wsl', '--prepare-only', '--clients', ''], env={**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'ATLASBRAIN_INSTALL_DIR': str(root)}, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ['-m', 'atlasbrain.desktop_install', '--vault', str(project), '--clients', '', '--port', '8765', '--platform', 'wsl', '--prepare-only']


def test_desktop_shortcut_opens_selected_project(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    vault = tmp_path / "project with spaces & accents é"
    path = install.desktop_shortcut(vault, 9876)
    url = plistlib.loads(path.read_bytes())['URL']
    assert urlparse(url).netloc == '127.0.0.1:9876'
    assert parse_qs(urlparse(url).query) == {'v': [str(vault)]}


def test_global_configuration_removes_only_legacy_project_entries(tmp_path):
    vault = tmp_path / 'project'
    vault.mkdir()
    (vault / '.mcp.json').write_text('{"mcpServers":{"atlasbrain":{"type":"http","url":"http://old/projects/id/mcp"},"other":{"command":"keep"}}}')
    (vault / '.codex').mkdir()
    (vault / '.codex/config.toml').write_text('[mcp_servers.atlasbrain]\nurl="http://old/projects/id/mcp"\n[mcp_servers.other]\ncommand="keep"\n')
    home = tmp_path / 'client-home'
    url = install.configure_clients(vault, ['codex', 'claude-code'], 'atlasbrain', 9876, client_home=home)
    assert url.endswith(':9876/mcp')
    assert json.loads((vault / '.mcp.json').read_text())['mcpServers'] == {'other': {'command': 'keep'}}
    assert tomllib.loads((vault / '.codex/config.toml').read_text())['mcp_servers'] == {'other': {'command': 'keep'}}
    assert json.loads((home / '.claude.json').read_text())['mcpServers']['atlasbrain']['url'] == url


def test_bootstrap_uses_its_checkout_and_separate_environment(tmp_path):
    import os
    import subprocess
    import shutil
    root = tmp_path / "checkout with spaces é"
    (root / 'scripts').mkdir(parents=True)
    (root / 'atlasbrain').mkdir()
    (root / 'atlasbrain/desktop_install.py').touch()
    (root / 'pyproject.toml').touch()
    (root / '.git').mkdir()
    script = root / 'scripts/install.sh'
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/install.sh', script)
    environment = root / '.venv-wsl'
    (environment / 'bin').mkdir(parents=True)
    executable = environment / 'bin/python'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    executable.chmod(0o700)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text('#!/bin/sh\nexit 0\n')
    uv.chmod(0o700)
    vault = tmp_path / 'notes'
    vault.mkdir()
    env = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'UV_PROJECT_ENVIRONMENT': str(environment)}
    env.pop('ATLASBRAIN_INSTALL_DIR', None)
    result = subprocess.run(['bash', str(script), '--vault', str(vault), '--platform', 'wsl', '--prepare-only'], env=env, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines()[:2] == ['-m', 'atlasbrain.desktop_install']
    assert not (root / '.venv').exists()


@pytest.mark.parametrize('arguments, message', [(['--port', '0'], 'Port must'), (['--vault'], 'Missing value'), (['--platform', 'other'], 'Platform must')])
def test_bootstrap_rejects_invalid_arguments_before_installing(arguments, message):
    import subprocess
    script = Path(__file__).resolve().parents[1] / 'scripts/install.sh'
    result = subprocess.run(['bash', str(script), *arguments], text=True, capture_output=True)
    assert result.returncode == 2
    assert message in result.stderr


def test_opencode_global_jsonc_is_not_shadowed(tmp_path):
    home = tmp_path / 'home'
    config = home / '.config/opencode/opencode.jsonc'
    config.parent.mkdir(parents=True)
    config.write_text('// existing settings\n{}')
    with pytest.raises(ValueError, match='opencode.jsonc'):
        install.configure_clients(tmp_path, ['opencode'], 'atlasbrain', 8765, client_home=home)
    assert not config.with_suffix('.json').exists()


def test_removing_the_only_codex_server_leaves_a_valid_file(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[mcp_servers.atlasbrain]\nurl = "http://old/projects/id/mcp"\n')
    install.codex_config(path, 'atlasbrain', None)
    assert 'atlasbrain' not in tomllib.loads(path.read_text()).get('mcp_servers', {})
