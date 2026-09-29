import io
import json
from types import SimpleNamespace

import pytest

from atlasbrain.cli import _guard_relevant, _merge_hook, _remove_owned_hooks, cmd_guard, cmd_hooks


@pytest.mark.parametrize('tool,command,relevant', [
    ('Read', '', True), ('Glob', '', True), ('Grep', '', True),
    ('Bash', 'rg --files src', True), ('Bash', 'git status && rg symbol .', True),
    ('Bash', 'cat README.md', True), ('Bash', 'env FOO=bar /usr/bin/grep foo src.py', True),
    ('exec_command', 'find . -name main.py', True),
    ('Bash', 'echo "rg should not trigger"', False), ('Bash', 'git status', False),
    ('Bash', 'uv run pytest', False), ('mcp__atlasbrain__arquivos', '', False),
])
def test_guard_discriminates_tool_intent(tool, command, relevant):
    assert _guard_relevant({'tool_name': tool, 'tool_input': {'command': command}}) is relevant


def test_guard_project_scope_cooldown_and_invalid_input(projeto, tmp_path, monkeypatch, capsys):
    (projeto / '.atlasbrain').mkdir()
    def run(cwd, tool='Read'):
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'cwd': str(cwd), 'session_id': 'same', 'tool_name': tool})))
        cmd_guard(None)
        return capsys.readouterr().out
    first = json.loads(run(projeto))['hookSpecificOutput']
    assert first['hookEventName'] == 'PreToolUse' and 'additionalContext' in first
    assert 'permissionDecision' not in first
    assert run(projeto) == ''
    other = tmp_path / 'other'
    (other / '.atlasbrain').mkdir(parents=True)
    assert run(other)
    assert run(tmp_path) == ''
    monkeypatch.setattr('sys.stdin', io.StringIO('invalid'))
    cmd_guard(None)
    assert capsys.readouterr().out == ''


def test_install_remove_preserves_unrelated_handlers(tmp_path):
    p = tmp_path / 'hooks.json'
    other = {'type': 'command', 'command': 'keep-other-hook'}
    old = {'type': 'command', 'command': '"/old/atlasbrain" guard'}
    p.write_text(json.dumps({'unrelated': True, 'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [other, old]}]}}))
    for _ in range(2):
        _merge_hook(p, 'PreToolUse', '"/new/atlasbrain" guard')
    data = json.loads(p.read_text())
    entries = data['hooks']['PreToolUse']
    assert data['unrelated'] and len(entries) == 2
    assert entries[0]['hooks'] == [other]
    assert entries[1]['matcher'] == 'Bash|Read|Grep|Glob'
    assert _remove_owned_hooks(entries) == [{'matcher': 'Bash', 'hooks': [other]}]


def test_installer_covers_both_clients(tmp_path, monkeypatch):
    from pathlib import Path
    home = Path.home()
    for name in ('.claude', '.codex'):
        (home / name).mkdir()
    monkeypatch.setattr('atlasbrain.cli.shutil.which', lambda _: '/installed/atlasbrain')
    cmd_hooks(SimpleNamespace(vault=None, remover=False))
    for p in (home / '.claude/settings.json', home / '.codex/hooks.json'):
        guard = json.loads(p.read_text())['hooks']['PreToolUse'][0]
        assert 'Bash' in guard['matcher'] and guard['hooks'][0]['command'] == '"/installed/atlasbrain" guard'
