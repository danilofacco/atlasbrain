"""Markdown editing with optimistic revisions and one recoverable previous version."""
import fcntl
import hashlib
import json
import os
import stat
import uuid
import time
import re
import difflib
import threading
from functools import wraps
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .config import BRAIN, IGNORE_DIRS, data_dir

MAX_BYTES = 200_000


class EditError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _file(vault, path, *, markdown_only=True):
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        raise EditError('Caminho inválido')
    relative = PurePosixPath(path)
    if relative.is_absolute() or any(p in ('', '.', '..') for p in path.split('/')):
        raise EditError('Use um caminho relativo dentro do projeto')
    if markdown_only and relative.suffix.lower() not in ('.md', '.markdown'):
        raise EditError('O editor aceita apenas Markdown')
    if relative.name.startswith('.') or any(p in IGNORE_DIRS for p in relative.parts[:-1]):
        raise EditError('Arquivos ocultos e pastas geradas não podem ser editados')
    if any(p.startswith('.') and p != BRAIN for p in relative.parts[:-1]):
        raise EditError('Pastas internas não podem ser editadas')
    if tuple(p.lower() for p in relative.parts) == (BRAIN.lower(), 'relatorio.md'):
        raise EditError('O relatório é gerado automaticamente')
    file = vault.resolve()
    for part in relative.parts:
        file = file / part
        if file.is_symlink():
            raise EditError('Links simbólicos não podem ser editados')
    if not file.resolve().is_relative_to(vault.resolve()):
        raise EditError('Caminho fora do projeto')
    return file, relative.as_posix()


def _decode(raw, max_bytes=MAX_BYTES):
    if len(raw) > max_bytes:
        raise EditError('Arquivo maior que o limite do editor (200 KB)', 413)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise EditError('O editor aceita arquivos UTF-8')


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _cache(vault):
    if (vault / BRAIN).is_symlink():
        raise EditError('Pasta do cérebro inválida')
    root = data_dir(vault) / '.editor-backups'
    if root.is_symlink():
        raise EditError('Pasta de recuperação inválida')
    root.mkdir(exist_ok=True)
    return root


_mutex = threading.RLock()
_local = threading.local()


@contextmanager
def _lock(vault):
    # Reentrant in one thread, serialized across threads and processes.
    key = str(vault.resolve())
    with _mutex:
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        with (_cache(vault) / 'editor.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                fcntl.flock(handle, fcntl.LOCK_UN)


def serialized(function):
    @wraps(function)
    def wrapped(vault, *args, **kwargs):
        with _lock(vault):
            return function(vault, *args, **kwargs)
    return wrapped


def operation(vault, operation_id, payload, action):
    """Durable retry receipt. An interrupted operation is never blindly replayed."""
    if not operation_id:
        return action()
    if not isinstance(operation_id, str) or len(operation_id) > 200:
        raise EditError('Identificador de operação inválido')
    signature = revision(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    with _lock(vault):
        file = _cache(vault) / ('operation-' + revision(operation_id.encode()) + '.json')
        if file.exists():
            receipt = json.loads(file.read_text())
            if receipt['signature'] != signature:
                raise EditError('Identificador já usado para outra operação', 409)
            if receipt['state'] != 'done':
                raise EditError('Operação interrompida: confira o arquivo antes de tentar com outro identificador', 409)
            return receipt['result']
        receipt = dict(signature=signature, state='pending')
        _atomic(file, json.dumps(receipt).encode(), mode=0o600)
        result = action()
        receipt.update(state='done', result=result)
        _atomic(file, json.dumps(receipt, ensure_ascii=False).encode(), mode=0o600)
        return result


def _backup(vault, path):
    return _cache(vault) / (hashlib.sha256(path.encode()).hexdigest()+'.bak')


def read(vault, path, previous=False):
    file, path = _file(vault, path)
    source = _backup(vault, path) if previous else file
    if not source.is_file() or source.is_symlink():
        raise EditError('Versão anterior indisponível' if previous else 'Arquivo não encontrado', 404)
    if source.stat().st_size > MAX_BYTES:
        raise EditError('Arquivo maior que o limite do editor (200 KB)',413)
    raw = source.read_bytes()
    return dict(path=path, content=_decode(raw), revision=revision(raw), previous=previous,
                recuperavel=_backup(vault, path).is_file())


def _atomic(file, raw, mode=0o644, create=False):
    temporary = file.parent / ('.'+file.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        if create:
            # Atomic no-clobber create: another application may create the same name.
            os.link(temporary, file)
        else:
            temporary.replace(file)
    finally:
        temporary.unlink(missing_ok=True)


def save(vault, path, content, base_revision=None, create=False, *, max_bytes=MAX_BYTES):
    if not isinstance(content,str):
        raise EditError('Conteúdo inválido')
    try:
        raw = content.encode('utf-8')
    except UnicodeEncodeError:
        raise EditError('O editor aceita conteúdo UTF-8 válido')
    if len(raw)>max_bytes:
        raise EditError('Conteúdo maior que 200 KB',413)
    with _lock(vault):
        file, path = _file(vault, path)
        old = None
        if create:
            if file.exists():
                raise EditError('Já existe um arquivo com esse nome. Escolha outro.', 409)
        else:
            if not file.is_file():
                raise EditError('O arquivo foi removido ou não existe mais', 409)
            if file.stat().st_size>max_bytes:
                raise EditError('Arquivo maior que 200 KB',413)
            old = file.read_bytes()
            _decode(old, max_bytes)
            if not base_revision or revision(old) != base_revision:
                raise EditError('O arquivo mudou fora do editor. Recarregue a versão atual antes de salvar.', 409)
            if old == raw:
                return dict(path=path, revision=revision(raw), recuperavel=_backup(vault,path).exists(), alterado=False)
        file.parent.mkdir(parents=True, exist_ok=True)
        # Recheck components after folder creation and immediately before replacement.
        _file(vault, path)
        if old is not None:
            if file.read_bytes() != old:
                raise EditError('O arquivo mudou durante o salvamento. Recarregue antes de salvar.',409)
            _atomic(_backup(vault,path), old, mode=0o600)
            _snapshot(vault, path, old)
        try:
            _atomic(file, raw, mode=stat.S_IMODE(file.stat().st_mode) if old is not None else 0o644, create=create)
        except FileExistsError:
            raise EditError('Já existe um arquivo com esse nome',409)
    return dict(path=path, revision=revision(raw), recuperavel=old is not None, alterado=True)


def link_candidates(con, query='', exclude='', limit=12):
    """Bounded suggestions from the complete index, with unambiguous path targets."""
    import json
    needle = str(query).strip().casefold()[:200]
    items = []
    for row in con.execute('SELECT path,title,aliases,kind FROM notes WHERE path!=? ORDER BY path', (exclude,)):
        aliases = json.loads(row['aliases'] or '[]')
        names = [row['path'], row['title'] or '', *aliases]
        if needle and not any(needle in name.casefold() for name in names):
            continue
        target = row['path']
        if Path(target).suffix.lower() in ('.md', '.markdown'):
            target = str(PurePosixPath(target).with_suffix(''))
        if any(c in target for c in '[]|#\n\r'):
            continue
        exact = any(name.casefold() == needle for name in names)
        prefix = any(name.casefold().startswith(needle) for name in names)
        items.append((not exact, not prefix, row['path'], dict(path=row['path'], title=row['title'], target=target, kind=row['kind'])))
    items.sort(key=lambda x: x[:3])
    return [item[3] for item in items[:max(1, min(int(limit), 30))]]


def resolve_reference(con, reference):
    """Resolve wiki targets using the same precedence as the indexer."""
    import json
    raw = str(reference).split('|', 1)[0].split('#', 1)[0].strip().lower()
    target = raw.removesuffix('.md')
    rows = sorted(con.execute('SELECT path,title,aliases FROM notes').fetchall(), key=lambda r: len(r['path']))
    tests = [lambda r: r['path'].lower() == raw,
             lambda r: str(PurePosixPath(r['path']).with_suffix('')).lower() == target,
             lambda r: PurePosixPath(r['path']).stem.lower() == PurePosixPath(target).name,
             lambda r: target in [(r['title'] or '').lower(), *[a.lower() for a in json.loads(r['aliases'] or '[]')]]]
    for test in tests:
        for row in rows:
            if test(row):
                return dict(path=row['path'], title=row['title'])
    return None


def delete(vault, path):
    """Permanently unlink one file and its editor recovery versions."""
    with _lock(vault):
        file, path = _file(vault, path, markdown_only=False)
        if not file.is_file():
            raise EditError('Arquivo não encontrado', 404)
        backup = _backup(vault, path)
        history_dir = _cache(vault) / ('history-' + revision(path.encode()))
        if history_dir.is_symlink() or backup.is_symlink():
            raise EditError('Pasta de recuperação inválida')
        versions = list(history_dir.glob('*.md')) if history_dir.exists() else []
        if any(p.is_symlink() or not p.is_file() for p in versions):
            raise EditError('Histórico inválido')
        for version in versions:
            version.unlink()
        if history_dir.exists():
            history_dir.rmdir()
        backup.unlink(missing_ok=True)
        _file(vault, path, markdown_only=False)
        file.unlink()
    return {'path': path, 'removido': True, 'permanente': True}


def _history_dir(vault, path):
    root = _cache(vault) / ('history-' + revision(path.encode()))
    if root.is_symlink():
        raise EditError('Histórico inválido')
    root.mkdir(exist_ok=True)
    return root


def _snapshot(vault, path, raw):
    root = _history_dir(vault, path)
    file = root / (str(time.time_ns()) + '.md')
    _atomic(file, raw, mode=0o600, create=True)
    for old in sorted(root.glob('*.md'), reverse=True)[30:]:
        old.unlink()


def history(vault, path, version=None):
    _, path = _file(vault, path)
    root = _history_dir(vault, path)
    if version is None:
        return {'path': path, 'versoes': [{'id': p.stem, 'data': int(p.stem)/1e9, 'bytes': p.stat().st_size}
                for p in sorted(root.glob('*.md'), reverse=True)]}
    if not re.fullmatch(r'[0-9]{10,25}', str(version)):
        raise EditError('Versão inválida')
    file = root / (str(version) + '.md')
    if not file.is_file() or file.is_symlink():
        raise EditError('Versão não encontrada', 404)
    content = _decode(file.read_bytes())
    current = read(vault, path)
    diff = ''.join(difflib.unified_diff(current['content'].splitlines(True), content.splitlines(True),
                                       fromfile='atual', tofile='versão escolhida'))
    return {'path': path, 'content': content, 'revision': current['revision'], 'diff': diff}


def restore(vault, path, version, base_revision):
    with _lock(vault):
        previous = history(vault, path, version)
        return save(vault, path, previous['content'], base_revision)


def section_content(content, section, text):
    """Replace exactly one H2 section, ignoring headings inside fenced code."""
    if not isinstance(section, str) or not section.strip() or '\n' in section:
        raise EditError('Nome da seção inválido')
    lines = content.splitlines(keepends=True)
    headings = []
    fence = None
    frontmatter = bool(lines and lines[0].strip() == '---')
    for i, line in enumerate(lines):
        if frontmatter:
            if i and line.strip() == '---':
                frontmatter = False
            continue
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        match = re.match(r'^ {0,3}(#{1,2}) +(.+?)\s*#*\s*$', line)
        if match:
            headings.append((i, len(match[1]), match[2]))
    matches = [h for h in headings if h[1] == 2 and h[2] == section.strip()]
    if len(matches) > 1:
        raise EditError('Seção repetida: use a edição completa para desambiguar', 409)
    replacement = '## ' + section.strip() + '\n\n' + text.strip() + '\n\n'
    if not matches:
        return content.rstrip() + '\n\n' + replacement
    start = matches[0][0]
    end = next((h[0] for h in headings if h[0] > start), len(lines))
    return ''.join(lines[:start]) + replacement + ''.join(lines[end:])


def update_section(vault, path, section, text, base_revision, operation_id=None):
    def apply():
        with _lock(vault):
            current = read(vault, path)
            return save(vault, path, section_content(current['content'], section, text), base_revision)
    return operation(vault, operation_id, ['section', path, section, text, base_revision], apply)
