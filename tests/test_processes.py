import subprocess
import sys
from types import SimpleNamespace

import pytest
from atlasbrain import capture, config, indexer, processes, update


@pytest.fixture
def windows_options(monkeypatch):
    monkeypatch.setattr(processes.sys, 'platform', 'win32')
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200, raising=False)


def test_windows_commands_hide_console_and_workers_keep_process_group(windows_options):
    assert processes.hidden_options() == {'creationflags': 0x08000000}
    assert processes.background_options() == {'creationflags': 0x08000200}


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
def test_unix_commands_and_workers_keep_existing_behavior(monkeypatch, platform):
    monkeypatch.setattr(processes.sys, 'platform', platform)
    assert processes.hidden_options() == {}
    assert processes.background_options() == {'start_new_session': True}


def test_repeated_project_detection_scan_and_update_hide_windows_console(tmp_path, monkeypatch, windows_options):
    (tmp_path / '.git').mkdir()
    calls = []
    def run(command, **options):
        assert options['creationflags'] & 0x08000000
        calls.append(command)
        output = str(tmp_path) if 'rev-parse' in command else b'note.md\0' if 'ls-files' in command else 'updated'
        return SimpleNamespace(stdout=output, returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    for _ in range(3):
        assert config.git_root(tmp_path) == tmp_path.resolve()
        assert indexer._git_files(tmp_path) == ['note.md']
    assert update._run(['uv', 'sync', '--locked'], tmp_path) == 'updated'
    assert len(calls) == 7


@pytest.mark.parametrize('engine', ['claude', 'codex'])
def test_automatic_capture_hides_ai_cli_console(monkeypatch, windows_options, engine):
    monkeypatch.setenv('ATLASBRAIN_CAPTURA_MOTOR', engine)
    def run(command, **options):
        assert command[0] == engine
        assert ('--no-session-persistence' if engine == 'claude' else '--ephemeral') in command
        assert options['creationflags'] & 0x08000000
        assert options['input'] == 'Extract notes'
        return SimpleNamespace(stdout='{"notes": []}')
    monkeypatch.setattr(subprocess, 'run', run)
    assert capture._extract('Extract notes') == {'notes': []}


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows console handle')
@pytest.mark.parametrize('options', [processes.hidden_options, processes.background_options])
def test_native_windows_child_has_no_console(options):
    result = subprocess.run([sys.executable, '-c',
                             'import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())'],
                            capture_output=True, text=True, timeout=15, check=True, **options())
    assert result.stdout.strip() == '0'
