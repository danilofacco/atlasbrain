import hashlib
import inspect
import json
import sys

import pytest

from atlasbrain import cli, config, editor, tools, url_import
from atlasbrain.consolidation import _summary_path
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault, scan
from atlasbrain.mcp_api import LEGACY_SIGNATURES, PARAMETERS
from atlasbrain.memory import record_decision


def test_native_tools_use_the_public_english_parameters():
    for name, parameters in LEGACY_SIGNATURES.values():
        signature = inspect.signature(getattr(tools, name))
        assert set(signature.parameters) - {'ctx'} == set(parameters.values())
        assert all(old == current or current not in PARAMETERS for old, current in parameters.items())


def test_legacy_registry_is_read_and_new_registration_preserves_it(project):
    config.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    legacy = config.REGISTRY.with_name('projetos.json')
    legacy.write_text(json.dumps({str(project): {'nome': 'Legacy', 'visto': 1, 'global': False}}))
    assert config._read_registry()[str(project)]['nome'] == 'Legacy'
    config.register(project)
    assert config.REGISTRY.exists()
    assert str(project) in config._read_registry()
    assert legacy.exists()


def test_existing_portuguese_decision_remains_readable_and_not_duplicated(project):
    result = record_decision(project, 'Usar SQLite no MVP', 'Banco local SQLite.', force=True)
    current = project / result['path']
    previous = project / '.atlasbrain' / 'Decisões' / current.name
    previous.parent.mkdir()
    current.rename(previous)
    index_vault(project, quiet=True)
    repeated = record_decision(project, 'Usar SQLite no MVP', 'Banco local SQLite.')
    assert not repeated['criada']
    assert previous.exists()
    assert '.atlasbrain/Decisões/' in repeated['path']
    assert not list(current.parent.glob('*.md'))


def test_import_and_summary_reuse_existing_portuguese_paths(project, monkeypatch):
    url = 'https://example.com/reference'
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    previous = project / '.atlasbrain' / 'Importações' / f'{key}.md'
    previous.parent.mkdir(parents=True)
    previous.write_text('# Existing import\n')
    monkeypatch.setattr(url_import, 'fetch', lambda _: pytest.fail('Already imported URL was fetched'))
    assert url_import.import_url(project, url)['path'] == previous.relative_to(project).as_posix()
    group = 'a' * 20
    summary = project / '.atlasbrain' / 'Sinteses' / f'{group}.md'
    summary.parent.mkdir()
    summary.write_text('# Summary\n')
    assert _summary_path(project, group) == summary.relative_to(project).as_posix()


@pytest.mark.parametrize('name', ['REPORT.md', 'RELATORIO.md'])
def test_generated_reports_are_excluded_and_protected(project, name):
    path = project / '.atlasbrain' / name
    path.parent.mkdir(exist_ok=True)
    path.write_text('# Generated report\n')
    assert path.relative_to(project).as_posix() not in scan(project)
    with pytest.raises(editor.EditError):
        editor.save(project, path.relative_to(project).as_posix(), '# Overwrite\n')


@pytest.mark.parametrize('flags', [('--port', '--name'), ('--porta', '--nome')])
def test_setup_english_flags_and_legacy_aliases_store_the_same_values(project, monkeypatch, capsys, flags):
    from atlasbrain import service
    launches = []
    def start(vault, port, auto_index):
        launches.append((vault, port, auto_index))
        return {'pid': 1, 'port': port}
    monkeypatch.setattr(service, 'start', start)
    monkeypatch.setattr(sys, 'argv', ['atlasbrain', 'setup', '--vault', str(project), '--client', 'codex', flags[0], '8888', flags[1], 'my-project'])
    cli.main()
    assert launches == [(project.resolve(), 8888, True)]
    assert '[mcp_servers."my-project"]' in capsys.readouterr().out


def test_global_project_discovery_preserves_both_wire_contracts(project):
    import asyncio
    from atlasbrain.mcp_server import build_server
    index_vault(project, quiet=True)
    config.GLOBAL_BRAIN.mkdir(parents=True, exist_ok=True)
    async def run():
        server = build_server(config.GLOBAL_BRAIN, auto_index=False)
        assert 'projects' in {tool.name for tool in await server.list_tools()}
        assert 'projetos' not in {tool.name for tool in await server.list_tools()}
        current = json.loads((await server.call_tool('projects', {})).content[0].text)
        previous = json.loads((await server.call_tool('projetos', {})).content[0].text)
        assert str(project) in {entry['folder'] for entry in current['projects']}
        assert str(project) in {entry['pasta'] for entry in previous['projetos']}
    asyncio.run(run())
