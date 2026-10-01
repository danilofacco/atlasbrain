import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen

from atlasbrain import config, indexer, service


def test_configs(project):
    url = '/mcp'
    assert json.loads(service.client_config(project, 'antigravity'))['mcpServers']['atlasbrain']['serverUrl'].endswith(url)
    assert json.loads(service.client_config(project, 'claude'))['mcpServers']['atlasbrain']['type'] == 'http'
    import tomllib
    assert tomllib.loads(service.client_config(project, 'codex'))['mcp_servers']['atlasbrain']['url'].endswith(url)


def test_shared_daemon_and_http_sessions(project, tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    monkeypatch.setenv('ATLASBRAIN_NO_EMBED', '1')
    # The child uses its own HOME registry, while config was already imported here.
    from atlasbrain import config
    monkeypatch.setattr(config, 'REGISTRY', tmp_path / 'home' / '.config' / 'atlasbrain' / 'projects.json')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    service.state_dir().mkdir(parents=True)
    (service.state_dir() / 'server.json').write_text(json.dumps({'pid': 999999, 'port': port, 'instance': 'stale'}))
    try:
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(lambda _: service.start(project, port, False), range(3)))
        assert len({r['pid'] for r in results}) == 1
        from atlasbrain.desktop_install import check_mcp
        check_mcp(f'http://127.0.0.1:{port}/mcp')
        with urlopen(f'http://127.0.0.1:{port}/') as r:
            assert b'<html' in r.read().lower()
        original_health = service.owned_health
        attempts = [0]
        def initially_busy():
            attempts[0] += 1
            return None if attempts[0] == 1 else original_health()
        with monkeypatch.context() as scoped:
            scoped.setattr(service, 'owned_health', initially_busy)
            assert service.start(project, port, False)['pid'] == results[0]['pid']
        second = tmp_path / 'other'
        second.mkdir()
        service.start(second, port, False)
        async def run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            async def client(vault):
                url = f'http://127.0.0.1:{port}/projects/{service.project_id(vault)}/mcp'
                async with streamable_http_client(url) as transport:
                    async with ClientSession(transport[0], transport[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {'symbol_location','isolated_files'} <= {t.name for t in tools.tools}
                        if vault == second:
                            await session.call_tool('criar_nota', {'titulo': 'Isolation sentinel', 'conteudo': 'Only belongs to the second project.'})
                        result = await session.call_tool('recentes', {})
                        assert not getattr(result, 'is_error', getattr(result, 'isError', False))
            await asyncio.gather(client(project), client(project), client(second))
        asyncio.run(run())
        assert (second / '.atlasbrain' / 'Inbox' / 'Isolation sentinel.md').exists()
        assert not (project / '.atlasbrain' / 'Inbox' / 'Isolation sentinel.md').exists()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            try:
                service.start(project, occupied.getsockname()[1], False)
            except RuntimeError:
                pass
            else:
                assert False, 'must not create a second daemon on another port'
        assert service.owned_health()['pid'] == results[0]['pid']
        with urlopen(f'http://127.0.0.1:{port}/api/soltos?limite=1') as r:
            report=json.load(r)
            assert report['limite']==1 and len(report['itens'])<=1
        assert service.stop()
        restarted=service.start(project,port,False)
        assert restarted['pid']!=results[0]['pid'] and restarted['port']==port
    finally:
        service.stop()
    assert service.owned_health() is None
    log = (service.state_dir() / 'server.log').read_text()
    assert 'Application shutdown failed' not in log
    assert 'different task' not in log


def test_occupied_port_never_spawns(project, tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        import pytest
        with pytest.raises(RuntimeError, match='occupied'):
            service.start(project, occupied.getsockname()[1], False)
    assert not (service.state_dir() / 'server.json').exists()


def test_global_mcp_requires_project_folder_and_routes_to_it(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    monkeypatch.setenv('ATLASBRAIN_NO_EMBED', '1')
    monkeypatch.setattr(config, 'REGISTRY', tmp_path / 'home' / '.config' / 'atlasbrain' / 'projects.json')
    global_brain = config.GLOBAL_BRAIN
    global_brain.mkdir(parents=True)
    project = tmp_path / 'adsivos'
    project.mkdir()
    (project / 'README.md').write_text('# Adsivos\nBiblioteca de stickers para Stories.\n')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    try:
        service.start(global_brain, port, False)
        service.start(project, port, False)
        indexer.index_vault(project, quiet=True)

        async def run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            url = json.loads(service.client_config(global_brain, 'claude', port))['mcpServers']['atlasbrain']['url']
            async with streamable_http_client(url) as transport:
                async with ClientSession(transport[0], transport[1]) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    assert 'projects' in tools and 'projetos' not in tools
                    assert 'project_folder' in tools['report'].input_schema['properties']
                    missing = await session.call_tool('relatorio', {})
                    assert 'Informe `pasta_projeto`' in missing.content[0].text
                    assert 'relatório de SegundoCerebro' not in missing.content[0].text
                    selected = await session.call_tool('relatorio', {'pasta_projeto': str(project)})
                    assert 'relatório de adsivos' in selected.content[0].text
                    orphans = await session.call_tool('soltos', {'pasta_projeto': 'adsivos'})
                    report = json.loads(orphans.content[0].text)
                    assert report['total'] == 1
                    assert report['itens'][0]['path'] == 'README.md'
                    all_orphans = await session.call_tool('soltos', {})
                    assert json.loads(all_orphans.content[0].text)['total'] >= 1
        asyncio.run(run())
    finally:
        service.stop()


def test_global_endpoint_auto_routes_each_client_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    monkeypatch.setenv('ATLASBRAIN_NO_EMBED', '1')
    monkeypatch.setattr(config, 'REGISTRY', tmp_path / 'home/.config/atlasbrain/projects.json')
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    try:
        service.start(first, port, False)
        async def run():
            from mcp import ClientSession
            from mcp.types import ListRootsResult, Root
            from mcp.client.streamable_http import streamable_http_client
            async def client(folder, title):
                async def roots(context):
                    return ListRootsResult(roots=[Root(uri=folder.as_uri(), name=folder.name)])
                async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as streams:
                    async with ClientSession(streams[0], streams[1], list_roots_callback=roots) as session:
                        await session.initialize()
                        tools = {t.name: t for t in (await session.list_tools()).tools}
                        assert 'mcp_context' not in tools['create_note'].input_schema['properties']
                        result = await session.call_tool('create_note', {'title': title, 'content': 'Written from the client workspace.'})
                        assert not result.is_error
                        assert (folder / '.atlasbrain/Inbox' / (title + '.md')).exists(), result
                        # roots/list is consulted again on every call, so a changed workspace is respected.
                        moved = second if folder == first else first
                        folder = moved
                        result = await session.call_tool('create_note', {'title': title + ' moved', 'content': 'Changed workspace.'})
                        assert (moved / '.atlasbrain/Inbox' / (title + ' moved.md')).exists(), result
            await asyncio.gather(client(first, 'First sentinel'), client(second, 'Second sentinel'))
            async def ambiguous(context):
                return ListRootsResult(roots=[Root(uri=first.as_uri()), Root(uri=second.as_uri())])
            async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as streams:
                async with ClientSession(streams[0], streams[1], list_roots_callback=ambiguous) as session:
                    await session.initialize()
                    result = await session.call_tool('create_note', {'title': 'Must not guess', 'content': 'Ambiguous workspace.'})
                    assert 'Supply `project_folder`' in result.content[0].text
                    assert not (first / '.atlasbrain/Inbox/Must not guess.md').exists()
                    assert not (second / '.atlasbrain/Inbox/Must not guess.md').exists()
        asyncio.run(run())
        assert not (first / '.atlasbrain/Inbox/Second sentinel.md').exists()
        assert not (second / '.atlasbrain/Inbox/First sentinel.md').exists()
    finally:
        service.stop()


def test_workspace_uri_respects_nested_repositories_and_rejects_remote(tmp_path):
    from atlasbrain.mcp_server import _workspace_path
    parent = tmp_path / 'outer'
    parent.mkdir()
    config.data_dir(parent)
    nested = parent / 'inner'
    nested.mkdir()
    (nested / '.git').mkdir()
    assert _workspace_path(nested.as_uri()) == nested.resolve()
    assert _workspace_path('file://remote-server/project') is None
    assert _workspace_path('https://example.com/project') is None
    assert _workspace_path(Path.home().as_uri()) is None
