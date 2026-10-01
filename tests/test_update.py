import subprocess
from pathlib import Path

from atlasbrain import service, update


def test_updates_enabled_by_default_and_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    assert update.enabled()
    assert update.status()['enabled'] is True
    update.configure(False)
    assert update.enabled() is False
    update.configure(True)
    assert update.enabled()


def git(directory, *args):
    result = subprocess.run(['git', *args], cwd=directory, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def repositories(tmp_path):
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', str(remote))
    publisher = tmp_path / 'publisher'
    git(tmp_path, 'clone', str(remote), str(publisher))
    git(publisher, 'checkout', '-b', 'main')
    git(publisher, 'config', 'user.email', 'test@example.com')
    git(publisher, 'config', 'user.name', 'Test')
    (publisher / 'pyproject.toml').write_text('[project]\nname="atlasbrain"\nversion="0.1.0"\n')
    (publisher / 'uv.lock').write_text('version = 1\n')
    git(publisher, 'add', '.')
    git(publisher, 'commit', '-m', 'initial')
    git(publisher, 'push', '-u', 'origin', 'main')
    git(remote, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    installed = tmp_path / 'installed'
    git(tmp_path, 'clone', str(remote), str(installed))
    return publisher, installed


def publish(publisher: Path, version: str):
    (publisher / 'pyproject.toml').write_text(f'[project]\nname="atlasbrain"\nversion="{version}"\n')
    git(publisher, 'add', 'pyproject.toml')
    git(publisher, 'commit', '-m', version)
    git(publisher, 'push')


def test_update_only_new_version_clean_fast_forward_and_restarts_once(tmp_path, monkeypatch):
    publisher, installed = repositories(tmp_path)
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    assert update.check(installed)['state'] == 'up_to_date'
    publish(publisher, '0.1.1')
    assert update.check(installed)['state'] == 'available'
    running, events = [True], []
    identity = {'pid': 321, 'port': 8765, 'vault': str(tmp_path / 'brain'), 'auto_index': True}
    monkeypatch.setattr(service, 'owned_health', lambda: identity if running[0] else None)
    def stop():
        events.append('stop')
        running[0] = False
        return True
    def start(vault, port, auto_index):
        events.append(('start', Path(vault), port, auto_index))
        running[0] = True
        return identity
    monkeypatch.setattr(service, 'stop', stop)
    monkeypatch.setattr(service, 'start', start)
    monkeypatch.setattr(update, '_sync', lambda directory: events.append('sync'))
    result = update.apply(installed)
    assert result['state'] == 'applied' and result['version'] == '0.1.1'
    assert events == ['stop', 'sync', ('start', tmp_path / 'brain', 8765, True)]
    assert '0.1.1' in (installed / 'pyproject.toml').read_text()
    assert update.check(installed)['state'] == 'up_to_date'
    publish(publisher, '0.1.2')
    (installed / 'local-note.md').write_text('Keep my work')
    assert update.check(installed)['state'] == 'blocked'
    assert update.apply(installed)['state'] == 'blocked'
    assert (installed / 'local-note.md').read_text() == 'Keep my work'


def test_update_rolls_back_when_dependency_sync_fails(tmp_path, monkeypatch):
    publisher, installed = repositories(tmp_path)
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    old = git(installed, 'rev-parse', 'HEAD')
    publish(publisher, '0.1.1')
    monkeypatch.setattr(service, 'owned_health', lambda: None)
    calls = []
    def sync(directory):
        calls.append(True)
        if len(calls) == 1:
            raise update.UpdateError('dependency failure')
    monkeypatch.setattr(update, '_sync', sync)
    result = update.apply(installed)
    assert result['state'] == 'failed' and result['rolled_back']
    assert len(calls) == 2 and git(installed, 'rev-parse', 'HEAD') == old
    assert '0.1.0' in (installed / 'pyproject.toml').read_text()


def test_update_restores_old_service_when_new_start_fails(tmp_path, monkeypatch):
    publisher, installed = repositories(tmp_path)
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    old = git(installed, 'rev-parse', 'HEAD')
    publish(publisher, '0.1.1')
    identity = {'pid': 321, 'port': 8765, 'vault': str(tmp_path / 'brain'), 'auto_index': True}
    running, starts = [True], []
    monkeypatch.setattr(service, 'owned_health', lambda: identity if running[0] else None)
    def stop():
        running[0] = False
        return True
    def start(vault, port, auto_index):
        starts.append((Path(vault), port, auto_index))
        if len(starts) == 1:
            raise RuntimeError('new service failed')
        running[0] = True
        return identity
    monkeypatch.setattr(service, 'stop', stop)
    monkeypatch.setattr(service, 'start', start)
    monkeypatch.setattr(update, '_sync', lambda directory: None)
    result = update.apply(installed)
    assert result['state'] == 'failed' and result['rolled_back']
    assert len(starts) == 2 and starts[0] == starts[1] == (tmp_path / 'brain', 8765, True)
    assert git(installed, 'rev-parse', 'HEAD') == old and running[0]


def test_auto_loop_launches_one_temporary_helper_for_available_version(tmp_path, monkeypatch):
    class Done:
        calls = 0
        def wait(self, delay):
            self.calls += 1
            return self.calls > 1
    commands = []
    monkeypatch.setattr(update, 'root', lambda: tmp_path)
    monkeypatch.setattr(update, 'enabled', lambda: True)
    monkeypatch.setattr(update, 'check', lambda directory: {'state': 'available'})
    monkeypatch.setattr(service, 'state_dir', lambda: tmp_path)
    monkeypatch.setattr(update.subprocess, 'Popen', lambda args, **kwargs: commands.append((args, kwargs)))
    update.auto_loop(Done(), tmp_path / 'brain')
    assert len(commands) == 1
    assert commands[0][0][1:5] == ['-m', 'atlasbrain.cli', 'update', 'apply']
    from atlasbrain.processes import background_options
    assert all(commands[0][1][key] == value for key, value in background_options().items())
