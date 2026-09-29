import asyncio
import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen

from atlasbrain import service


def test_configs(projeto):
    url = f'/projects/{service.project_id(projeto)}/mcp'
    assert json.loads(service.client_config(projeto, 'antigravity'))['mcpServers']['atlasbrain']['serverUrl'].endswith(url)
    assert json.loads(service.client_config(projeto, 'claude'))['mcpServers']['atlasbrain']['type'] == 'http'
    import tomllib
    assert tomllib.loads(service.client_config(projeto, 'codex'))['mcp_servers']['atlasbrain']['url'].endswith(url)


def test_shared_daemon_and_http_sessions(projeto, tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    monkeypatch.setenv('ATLASBRAIN_NO_EMBED', '1')
    # The child uses its own HOME registry, while config was already imported here.
    from atlasbrain import config
    config.REGISTRY = tmp_path / 'home' / '.config' / 'atlasbrain' / 'projetos.json'
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    service.state_dir().mkdir(parents=True)
    (service.state_dir() / 'server.json').write_text(json.dumps({'pid': 999999, 'port': port, 'instance': 'stale'}))
    try:
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(lambda _: service.start(projeto, port, False), range(3)))
        assert len({r['pid'] for r in results}) == 1
        with urlopen(f'http://127.0.0.1:{port}/') as r:
            assert b'<html' in r.read().lower()
        original_health = service.owned_health
        attempts = [0]
        def initially_busy():
            attempts[0] += 1
            return None if attempts[0] == 1 else original_health()
        with monkeypatch.context() as scoped:
            scoped.setattr(service, 'owned_health', initially_busy)
            assert service.start(projeto, port, False)['pid'] == results[0]['pid']
        second = tmp_path / 'other'
        second.mkdir()
        service.start(second, port, False)
        async def run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            async def client(vault):
                url = json.loads(service.client_config(vault, 'claude', port))['mcpServers']['atlasbrain']['url']
                async with streamable_http_client(url) as transport:
                    async with ClientSession(transport[0], transport[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {'onde','soltos'} <= {t.name for t in tools.tools}
                        if vault == second:
                            await session.call_tool('criar_nota', {'titulo': 'Isolation sentinel', 'conteudo': 'Only belongs to the second project.'})
                        result = await session.call_tool('recentes', {})
                        assert not getattr(result, 'is_error', getattr(result, 'isError', False))
            await asyncio.gather(client(projeto), client(projeto), client(second))
        asyncio.run(run())
        assert (second / '.atlasbrain' / 'Inbox' / 'Isolation sentinel.md').exists()
        assert not (projeto / '.atlasbrain' / 'Inbox' / 'Isolation sentinel.md').exists()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            try:
                service.start(projeto, occupied.getsockname()[1], False)
            except RuntimeError:
                pass
            else:
                assert False, 'must not create a second daemon on another port'
        assert service.owned_health()['pid'] == results[0]['pid']
        with urlopen(f'http://127.0.0.1:{port}/api/soltos?limite=1') as r:
            report=json.load(r)
            assert report['limite']==1 and len(report['itens'])<=1
        assert service.stop()
        restarted=service.start(projeto,port,False)
        assert restarted['pid']!=results[0]['pid'] and restarted['port']==port
    finally:
        service.stop()
    assert service.owned_health() is None
    log = (service.state_dir() / 'server.log').read_text()
    assert 'Application shutdown failed' not in log
    assert 'different task' not in log


def test_occupied_port_never_spawns(projeto, tmp_path, monkeypatch):
    monkeypatch.setenv('ATLASBRAIN_SERVICE_DIR', str(tmp_path / 'service'))
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        import pytest
        with pytest.raises(RuntimeError, match='occupied'):
            service.start(projeto, occupied.getsockname()[1], False)
    assert not (service.state_dir() / 'server.json').exists()
