"""Version-gated updates for a clean Git clone running the shared service."""

from . import locking
import json
import os
import re
import subprocess
from .processes import background_options, hidden_options
import sys
import time
import tomllib
from pathlib import Path


CHECK_INTERVAL = 6 * 60 * 60
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class UpdateError(RuntimeError):
    pass


def root() -> Path:
    return Path(__file__).resolve().parent.parent


def _state_dir() -> Path:
    from .service import state_dir
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding="utf-8")
    temporary.replace(path)


def enabled() -> bool:
    # A ausência de configuração mantém as atualizações automáticas ligadas.
    return _read_json(_state_dir() / 'updates.json').get('enabled') is not False


def configure(value: bool) -> dict:
    policy = {'enabled': bool(value), 'interval_hours': CHECK_INTERVAL // 3600}
    _write_json(_state_dir() / 'updates.json', policy)
    return policy


def _record(result: dict) -> dict:
    _write_json(_state_dir() / 'update-status.json', {**result, 'checked_at': time.time()})
    return result


def status() -> dict:
    return {'enabled': enabled(), 'last': _read_json(_state_dir() / 'update-status.json')}


def _run(args: list[str], directory: Path, timeout: int = 60) -> str:
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'UV_PROJECT_ENVIRONMENT': sys.prefix,
           'PYTHONUTF8': '1'}
    try:
        result = subprocess.run(args, cwd=directory, env=env, capture_output=True, text=True,
                                timeout=timeout, check=False, **hidden_options())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f'Não foi possível executar {Path(args[0]).name}: {type(exc).__name__}') from exc
    if result.returncode:
        # Do not expose remote URLs or credentials that Git may echo to stderr.
        raise UpdateError(f'{Path(args[0]).name} falhou (código {result.returncode})')
    return result.stdout.strip()


def _git(directory: Path, *args: str) -> str:
    return _run(['git', *args], directory)


def _version(data: str) -> tuple[int, int, int]:
    try:
        value = tomllib.loads(data)['project']['version']
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError('pyproject.toml não contém versão válida') from exc
    match = VERSION_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise UpdateError('A atualização automática exige versão estável X.Y.Z')
    return tuple(int(part) for part in match.groups())


def _clean(directory: Path) -> bool:
    return not _git(directory, 'status', '--porcelain', '--untracked-files=normal')


def check(directory: Path | None = None) -> dict:
    """Fetch the configured upstream; only a higher stable version on a descendant is eligible."""
    directory = (directory or root()).resolve()
    if not (directory / '.git').exists() or not (directory / 'pyproject.toml').is_file():
        return _record({'state': 'unsupported', 'reason': 'Instalação precisa ser um clone Git com pyproject.toml.'})
    try:
        local = _version((directory / 'pyproject.toml').read_text(encoding="utf-8"))
        branch = _git(directory, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        upstream = _git(directory, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')
        if branch not in ('main', 'master') or '/' not in upstream:
            return _record({'state': 'blocked', 'reason': 'Atualização automática requer main/master com upstream configurado.'})
        remote, remote_branch = upstream.split('/', 1)
        if remote_branch != branch:
            return _record({'state': 'blocked', 'reason': 'A branch local não acompanha a mesma branch remota.'})
        if not _clean(directory):
            return _record({'state': 'blocked', 'version': '.'.join(map(str, local)),
                            'reason': 'Checkout com alterações locais; preserve-as antes de atualizar.'})
        tracking_ref = f'refs/remotes/{remote}/{branch}'
        _git(directory, 'fetch', '--quiet', '--no-tags', remote,
             f'+refs/heads/{branch}:{tracking_ref}')
        target = _git(directory, 'rev-parse', f'{tracking_ref}^{{commit}}')
        current = _git(directory, 'rev-parse', 'HEAD')
        try:
            remote_version = _version(_git(directory, 'show', f'{target}:pyproject.toml'))
            _git(directory, 'cat-file', '-e', f'{target}:uv.lock')
        except UpdateError as exc:
            return _record({'state': 'blocked', 'reason': str(exc)})
        local_text, remote_text = '.'.join(map(str, local)), '.'.join(map(str, remote_version))
        if remote_version <= local:
            return _record({'state': 'up_to_date', 'version': local_text, 'remote_version': remote_text})
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', current, target],
                                  cwd=directory, capture_output=True, timeout=15, **hidden_options())
        if ancestor.returncode:
            return _record({'state': 'blocked', 'version': local_text, 'remote_version': remote_text,
                            'reason': 'Histórico local e remoto divergiram; exige revisão manual.'})
        return _record({'state': 'available', 'version': local_text, 'remote_version': remote_text,
                        'current': current, 'target': target, 'remote': remote, 'branch': branch})
    except (UpdateError, OSError, subprocess.TimeoutExpired) as exc:
        return _record({'state': 'error', 'reason': str(exc)})


def _sync(directory: Path) -> None:
    _run(['uv', 'sync', '--locked', '--inexact'], directory, timeout=300)
    _run([sys.executable, '-m', 'atlasbrain.cli', '--help'], directory, timeout=30)


def apply(directory: Path | None = None, vault: Path | None = None) -> dict:
    """Fast-forward a clean clone, sync locked deps, restart once, roll back on failure."""
    from . import service
    directory = (directory or root()).resolve()
    with (_state_dir() / 'update.lock').open('a') as handle:
        locking.acquire(handle)
        plan = check(directory)
        if plan['state'] != 'available':
            return plan
        previous = service.owned_health()
        project = vault or (Path(previous['vault']) if previous and previous.get('vault') else None)
        if previous and project is None:
            return _record({'state': 'blocked', 'reason': 'Serviço antigo não informa o projeto inicial; reinicie-o antes de atualizar.'})
        stopped = False
        merged = False
        try:
            if previous:
                if not service.stop():
                    raise UpdateError('Não foi possível parar o serviço anterior.')
                stopped = True
            if not _clean(directory) or _git(directory, 'rev-parse', 'HEAD') != plan['current']:
                raise UpdateError('Checkout mudou durante a atualização; nenhuma alteração aplicada.')
            _git(directory, 'merge', '--ff-only', plan['target'])
            merged = True
            _sync(directory)
            if previous:
                service.start(project, previous['port'], previous['auto_index'])
            return _record({'state': 'applied', 'version': plan['remote_version'],
                            'previous_version': plan['version'], 'commit': plan['target']})
        except Exception as exc:
            rolled_back = False
            rollback_error = None
            try:
                live = service.owned_health() if previous else None
                if live and live.get('pid') != previous['pid']:
                    service.stop()
                if merged and _clean(directory) and _git(directory, 'rev-parse', 'HEAD') == plan['target']:
                    _git(directory, 'reset', '--hard', plan['current'])
                    _sync(directory)
                    rolled_back = True
            except Exception as recovery_exc:
                rollback_error = str(recovery_exc)
            if previous and not service.owned_health():
                try:
                    service.start(project, previous['port'], previous['auto_index'])
                except Exception as recovery_exc:
                    rollback_error = str(recovery_exc)
            return _record({'state': 'failed', 'reason': str(exc), 'rolled_back': rolled_back,
                            'rollback_error': rollback_error, 'service_was_stopped': stopped})


def auto_loop(done, vault: Path) -> None:
    """One background thread; a short-lived helper applies the update after checking."""
    directory = root()
    while not done.wait(60):
        if not enabled():
            continue
        result = check(directory)
        if result['state'] == 'available':
            from .service import state_dir
            with (state_dir() / 'server.log').open('ab') as log:
                subprocess.Popen([sys.executable, '-m', 'atlasbrain.cli', 'update', 'apply',
                                  '--vault', str(vault)], cwd=directory, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, **background_options())
        if done.wait(CHECK_INTERVAL - 60):
            break
