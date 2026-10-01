"""Exercise native locks and the shared service on every CI operating system."""
import errno
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from atlasbrain import desktop_install, indexer, locking, service


def test_lock_is_exclusive_across_processes_and_released_on_close(tmp_path):
    path = tmp_path / 'empty.lock'
    code = '''
import sys
from atlasbrain import locking
with open(sys.argv[1], 'a') as handle:
    locking.acquire(handle)
    print('ready', flush=True)
    sys.stdin.readline()
'''
    with subprocess.Popen([sys.executable, '-u', '-c', code, str(path)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == 'ready'
            with path.open('a') as handle:
                with pytest.raises(BlockingIOError):
                    locking.acquire(handle, blocking=False)
            child.stdin.write('\n')
            child.stdin.flush()
            child.wait(timeout=10)
            assert child.returncode == 0, child.stderr.read()
            with path.open('a') as handle:
                locking.acquire(handle, blocking=False)
                locking.release(handle)
        finally:
            if child.poll() is None:
                child.kill()


def test_busy_index_does_not_unlock_an_unowned_lock(tmp_path):
    with indexer._IndexLock(tmp_path) as acquired:
        assert acquired
        with indexer._IndexLock(tmp_path) as acquired_again:
            assert not acquired_again
        with (tmp_path / '.atlasbrain/index.lock').open('a') as handle:
            with pytest.raises(BlockingIOError):
                locking.acquire(handle, blocking=False)
    with indexer._IndexLock(tmp_path) as acquired:
        assert acquired


def test_windows_lock_normalizes_contention_and_waits(monkeypatch, tmp_path):
    calls = []
    busy = [True, False]
    def lock(fd, mode, count):
        calls.append((mode, count, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode == 2 and busy.pop(0):
            raise OSError(errno.EACCES, 'busy')
    monkeypatch.setattr(locking, '_WINDOWS', True)
    monkeypatch.setattr(locking, 'msvcrt', SimpleNamespace(locking=lock, LK_NBLCK=2, LK_UNLCK=0), raising=False)
    monkeypatch.setattr(locking.time, 'sleep', lambda seconds: None)
    with (tmp_path / 'lock').open('a') as handle:
        locking.acquire(handle)
        locking.release(handle)
        assert calls == [(2, 1, 0), (2, 1, 0), (0, 1, 0)]
        busy[:] = [True]
        with pytest.raises(BlockingIOError):
            locking.acquire(handle, blocking=False)


def test_windows_lock_preserves_real_io_errors(monkeypatch, tmp_path):
    def lock(*args):
        raise OSError(errno.EBADF, 'bad descriptor')
    monkeypatch.setattr(locking, '_WINDOWS', True)
    monkeypatch.setattr(locking, 'msvcrt', SimpleNamespace(locking=lock, LK_NBLCK=2), raising=False)
    with (tmp_path / 'lock').open('a') as handle:
        with pytest.raises(OSError) as error:
            locking.acquire(handle, blocking=False)
        assert error.value.errno == errno.EBADF


def test_windows_manifest_runner_mcp_and_authenticated_shutdown(tmp_path, monkeypatch):
    directory = tmp_path / "state ' accents é"
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(directory))
    monkeypatch.setenv('ATLASBRAIN_NO_EMBED', '1')
    project = tmp_path / "project ' accents é"
    project.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    manifest = desktop_install.startup_files(project, port, 'windows')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['environment']['UV_PROJECT_ENVIRONMENT'] == sys.prefix
    assert data['vault'] == str(project)
    child = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'atlasbrain.desktop_install', '--run-service', str(manifest)])
    try:
        deadline = time.monotonic() + 30
        while not service.owned_health() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail((directory / 'server.log').read_text(encoding='utf-8'))
            time.sleep(.1)
        live = service.owned_health()
        assert live and live['pid'] == child.pid
        assert 'shutdown_token' not in live
        desktop_install.check_mcp(f'http://127.0.0.1:{port}/mcp')
        request = Request(f'http://127.0.0.1:{port}/_shutdown', data=b'', method='POST')
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 403
        assert service.owned_health()['pid'] == child.pid
        # No process signal is required on either operating system.
        monkeypatch.setattr(service.os, 'kill', lambda *args: pytest.fail('unexpected OS signal'))
        assert service.stop()
        child.wait(timeout=10)
        assert child.returncode == 0
        assert not (directory / 'server.json').exists()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows drive URI')
def test_native_windows_workspace_uri(tmp_path):
    from atlasbrain.mcp_server import _workspace_path
    project = tmp_path / 'project with accents é'
    project.mkdir()
    (project / '.git').mkdir()
    assert _workspace_path(project.as_uri()) == project.resolve()


def test_non_git_nested_paths_use_forward_slashes(tmp_path):
    (tmp_path / 'notes/nested').mkdir(parents=True)
    (tmp_path / 'notes/nested/accent é.md').write_text('# Accents\n', encoding='utf-8')
    (tmp_path / 'notes/top.md').write_text('# Top\n', encoding='utf-8')
    assert set(indexer.scan(tmp_path)) == {'notes/nested/accent é.md', 'notes/top.md'}


def test_installer_rejects_foreign_listener_before_writing_settings(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        port = sock.getsockname()[1]
        vault = tmp_path / 'notes'
        vault.mkdir()
        monkeypatch.setattr(sys, 'argv', ['desktop_install', '--platform', 'windows', '--prepare-only', '--vault', str(vault), '--port', str(port)])
        monkeypatch.setattr(service, 'owned_health', lambda: None)
        with pytest.raises(RuntimeError, match='occupied'):
            desktop_install.main()
        assert not (Path.home() / '.codex/config.toml').exists()
        assert not (vault / '.atlasbrain').exists()


def test_stop_stale_state_does_not_signal_or_wait(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path))
    (tmp_path / 'server.json').write_text(json.dumps({'pid': 999999, 'port': 1, 'instance': 'stale'}), encoding='utf-8')
    monkeypatch.setattr(service, 'owned_health', lambda: None)
    monkeypatch.setattr(service.time, 'sleep', lambda *args: pytest.fail('stale process must not cause a delay'))
    assert service.stop() is False
