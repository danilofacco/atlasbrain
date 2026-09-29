import asyncio
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from atlasbrain import config


# ------------------------------------------------------------------ descoberta do projeto

def test_acha_raiz_do_git_a_partir_de_subpasta(projeto):
    assert config.resolve_vault(cwd=str(projeto / "src")) == projeto


def test_atlasbrain_existente_vence_o_git(projeto):
    sub = projeto / "web"
    (sub / ".atlasbrain").mkdir()
    assert config.resolve_vault(cwd=str(sub)) == sub.resolve()


def test_fora_de_projeto_usa_global(tmp_path):
    solta = tmp_path / "solta"
    solta.mkdir()
    assert config.resolve_vault(cwd=str(solta)) == config.GLOBAL_BRAIN.resolve()


def test_notas_no_global_ficam_na_raiz(projeto):
    assert config.notes_dir(projeto) == projeto / ".atlasbrain"
    g = config.resolve_vault(cwd=str(Path.home()))
    assert config.notes_dir(g) == g


def test_pastas_recusadas(tmp_path):
    home = Path.home().resolve()
    assert config.pasta_invalida(home)
    assert config.pasta_invalida(home.parent)
    assert config.pasta_invalida(Path("/"))
    assert config.pasta_invalida(Path("/System/Library"))
    assert config.pasta_invalida(tmp_path / "nao-existe")
    ok = tmp_path / "proj"
    ok.mkdir()
    assert config.pasta_invalida(ok) is None


def test_registro(projeto):
    config.data_dir(projeto)
    assert str(projeto) in {b["path"] for b in config.registered()}
    config.unregister(projeto)
    assert str(projeto) not in {b["path"] for b in config.registered()}


# ------------------------------------------------------------------ servidor web

@pytest.fixture
def servidor(indexado):
    from atlasbrain.web import criar_servidor
    projeto, _ = indexado
    srv = criar_servidor(projeto, port=0, auto_index=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", projeto
    srv.shutdown()


def req(url, metodo="GET", corpo=None, headers=None):
    r = urllib.request.Request(url, method=metodo, data=json.dumps(corpo).encode() if corpo is not None else None,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_web_grafo_e_busca(servidor):
    url, _ = servidor
    st, g = req(url + "/api/graph")
    assert st == 200 and g["modo"] == "arquivos" and any(n["path"] == "src/app.py" for n in g["nodes"])
    st, first = req(url + "/api/graph?limite=5")
    assert st == 200 and first["modo"] == "arquivos" and first["paginas"] > 1
    assert all(n["path"] and n["kind"] != "pasta" for n in first["nodes"])
    st, focused = req(url + "/api/graph?limite=5&foco=src/app.py")
    assert st == 200 and any(n["path"] == "src/app.py" for n in focused["nodes"])
    st, r = req(url + "/api/search?q=gloss%C3%A1rio")
    assert r[0]["path"] == "notas/Glossário.md"


def test_web_bloqueia_outro_site(servidor, tmp_path):
    url, _ = servidor
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    assert req(url + "/api/cerebros", "POST", {"path": str(alvo)})[0] == 403  # sem cabeçalho
    assert req(url + "/api/cerebros", "POST", {"path": str(alvo)},
               {"X-atlasbrain": "1", "Origin": "http://evil.com"})[0] == 403
    assert req(url + "/api/open?path=src/app.py")[0] == 403
    assert req(url + "/api/pastas?p=/")[0] == 403


def test_web_abre_pasta_valida_e_recusa_home(servidor, tmp_path):
    url, _ = servidor
    h = {"X-atlasbrain": "1"}
    st, r = req(url + "/api/cerebros", "POST", {"path": str(Path.home())}, h)
    assert st == 400 and "ampla" in r["erro"]
    nova = tmp_path / "nova"
    (nova / "src").mkdir(parents=True)
    (nova / "src" / "x.py").write_text("def x():\n    pass\n")
    st, r = req(url + "/api/cerebros", "POST", {"path": str(nova)}, h)
    assert st == 200
    for _ in range(100):
        t = req(url + "/api/brains?v=" + str(nova.resolve()))[1]["trabalhos"].get(str(nova.resolve()), {})
        if t.get("estado") in ("pronto", "erro"):
            break
        threading.Event().wait(0.1)
    assert t["estado"] == "pronto" and (nova / ".atlasbrain" / "index.db").exists()


# ------------------------------------------------------------------ MCP de ponta a ponta

def test_mcp_stdio(indexado):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    projeto, _ = indexado

    async def rodar():
        p = StdioServerParameters(command=sys.executable, args=["-m", "atlasbrain.cli", "mcp", "--sem-auto"],
                                  cwd=str(projeto / "src"), env={"ATLASBRAIN_NO_EMBED": "1", "HOME": str(Path.home()),
                                                                 "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                leitura = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
                onde = (await s.call_tool("onde", {"simbolo": "soma"})).content[0].text
                exp = (await s.call_tool("explicar", {"alvo": "src/app.py"})).content[0].text
                graph = (await s.call_tool("consultar_grafo", {"consulta": "src/app.py::main", "relacoes": ["chama"]})).content[0].text
                impact = (await s.call_tool("impacto", {"alvo": "src/util.py::soma"})).content[0].text
                graph_tool = next(t for t in tools if t.name == "consultar_grafo")
                schema = getattr(graph_tool, "input_schema", None) or getattr(graph_tool, "inputSchema")
                return leitura, onde, exp, graph, impact, schema

    leitura, onde, exp, graph, impact, schema = asyncio.run(rodar())
    assert {"buscar", "arquivos", "onde", "explicar", "consultar_grafo", "impacto"} <= leitura and "registrar_decisao" not in leitura
    assert "src/util.py" in onde and "src/app.py" in onde  # definição e uso
    assert "src/util.py" in exp and "EXTRACTED" in exp
    assert "src/util.py" in graph and "EXTRACTED" in graph
    assert "src/app.py" in impact and "evidência L9" in impact
    assert {"tokens", "modo", "direcao", "relacoes"} <= set(schema["properties"])


def test_web_intelligence_and_filters(servidor):
    url, _ = servidor
    for route in ('estado','mudancas','filtros'):
        status, report = req(url+'/api/'+route)
        assert status==200 and isinstance(report,dict)
    status, graph = req(url+'/api/graph?ext=.py&pasta=src&origem=EXTRACTED')
    assert status==200 and graph['nodes']
    assert all(n['path'].startswith('src/') and n['path'].endswith('.py') for n in graph['nodes'])
    assert all(e[4]=='EXTRACTED' for e in graph['edges'])
    status, report = req(url+'/api/sugestoes?path=src/busca.py')
    assert status==200 and report['sugestoes']


def test_declared_link_is_written_in_selected_project(servidor,tmp_path):
    from urllib.parse import quote
    from atlasbrain.config import register
    from atlasbrain.indexer import index_vault
    other=tmp_path/'other'
    other.mkdir()
    (other/'a.md').write_text('# A\n')
    (other/'b.md').write_text('# B\n')
    register(other)
    index_vault(other,quiet=True)
    url, default=servidor
    status, report=req(url+'/api/vinculo?v='+quote(str(other)), 'POST', {'de':'a.md','para':'b.md','motivo':'Reviewed relation'}, {'X-atlasbrain':'1'})
    assert status==200 and (other/report['path']).exists()
    assert not (default/report['path']).exists()
    status, graph=req(url+'/api/graph?v='+quote(str(other))+'&origem=DECLARED')
    assert status==200 and any(e[2]=='manual' and e[4]=='DECLARED' for e in graph['edges'])


def test_editor_api_conflict_preview_recovery_and_origin(servidor):
    url,vault=servidor
    headers={'X-atlasbrain':'1'}
    status,_=req(url+'/api/editor?path=notas/Gloss%C3%A1rio.md')
    assert status==403
    status,note=req(url+'/api/editor?path=notas/Gloss%C3%A1rio.md',headers=headers)
    assert status==200
    body={'path':note['path'],'content':'# Glossário\nNova versão','revision':note['revision']}
    status,_=req(url+'/api/editor','POST',body)
    assert status==403
    status,saved=req(url+'/api/editor','POST',body,headers)
    assert status==200 and saved['recuperavel']
    status,_=req(url+'/api/editor','POST',body,headers)
    assert status==409
    status,previous=req(url+'/api/editor?path=notas/Gloss%C3%A1rio.md&previous=1',headers=headers)
    assert status==200 and previous['content']==note['content']
    status,created=req(url+'/api/editor','POST',{'path':'.atlasbrain/Inbox/Visual.md','content':'# Visual\n[[Glossário]]','create':True},headers)
    assert status==200 and (vault/created['path']).exists()
    status,read=req(url+'/api/note?path=.atlasbrain/Inbox/Visual.md')
    assert status==200 and read['path']==created['path']


def test_editor_link_search_and_missing_reference(servidor):
    url,_=servidor
    status,data=req(url+'/api/editor/links?q=Gloss')
    assert status==200 and any(r['path']=='notas/Glossário.md' for r in data['itens'])
    status,data=req(url+'/api/editor/links?ref=Gloss%C3%A1rio')
    assert status==200 and data['nota']['path']=='notas/Glossário.md'
    status,data=req(url+'/api/editor/links?ref=NovaIdeia')
    assert status==200 and data['nota'] is None


def test_delete_file_api_requires_origin_and_removes_from_graph(servidor):
    url, vault = servidor
    body = {'path': 'src/app.py'}
    before = (vault / body['path']).read_bytes()
    status, _ = req(url+'/api/arquivo/excluir', 'POST', body)
    assert status == 403
    assert (vault / body['path']).exists()
    status, deleted = req(url+'/api/arquivo/excluir', 'POST', body, {'X-atlasbrain':'1'})
    assert status == 200 and deleted['removido']
    assert not (vault / body['path']).exists()
    assert deleted['permanente'] and 'recuperacao' not in deleted
    status, graph = req(url+'/api/graph')
    assert status == 200
    assert not any(n.get('path') == body['path'] and n['kind'] != 'fantasma' for n in graph['nodes'])
    status, _ = req(url+'/api/arquivo/excluir', 'POST', body, {'X-atlasbrain':'1'})
    assert status == 404


def test_history_restore_api(servidor):
    url,vault=servidor
    headers={'X-atlasbrain':'1'}
    from atlasbrain import editor
    original=editor.read(vault,'notas/Glossário.md')
    changed=editor.save(vault,original['path'],'# Changed',original['revision'])
    endpoint=url+'/api/historico?path=notas/Gloss%C3%A1rio.md'
    assert req(endpoint)[0]==403
    status,history=req(endpoint,headers=headers)
    assert status==200 and len(history['versoes'])==1
    version=history['versoes'][0]['id']
    status,preview=req(endpoint+'&version='+version,headers=headers)
    assert status==200 and preview['diff']
    body={'path':original['path'],'version':version,'revision':original['revision']}
    assert req(url+'/api/historico/restaurar','POST',body,headers)[0]==409
    body['revision']=changed['revision']
    assert req(url+'/api/historico/restaurar','POST',body,headers)[0]==200
    assert editor.read(vault,original['path'])['content']==original['content']


def test_trash_routes_are_removed(servidor):
    url, _ = servidor
    headers = {'X-atlasbrain':'1'}
    assert req(url+'/api/lixeira', headers=headers)[0] == 404
    assert req(url+'/api/lixeira/restaurar', 'POST', {'id':'trash-old'}, headers)[0] == 404
