import asyncio
import inspect
import json

import pytest

from atlasbrain import ferramentas as f
from atlasbrain.localization import is_english, language, text
from atlasbrain.mcp_api import PARAMETERS, TOOLS, internal_arguments, public_result
from atlasbrain.mcp_server import build_server


@pytest.mark.parametrize('name', list(TOOLS))
def test_each_english_parameter_maps_to_its_own_function(name):
    parameters = list(inspect.signature(getattr(f, name)).parameters)[1:]
    mapping = {PARAMETERS.get(p,p):p for p in parameters}
    public = {en:'sentinel' for en in mapping}
    # "target" means different internal parameters for path, explain and links.
    assert internal_arguments(name, public, mapping) == {p:'sentinel' for p in parameters}


def test_english_enums_and_labeled_questions():
    assert internal_arguments('consultar_grafo', {'direction':'incoming','relations':['calls','imports'],'depth':2}) == {'direcao':'entrada','relacoes':['chama','importa'],'profundidade':2}
    assert internal_arguments('contexto_tarefa', {'objective':'review'}) == {'objetivo':'revisar'}
    assert internal_arguments('revisar_decisao', {'action':'supersede'}) == {'acao':'substituir'}
    assert internal_arguments('recuperar_operacao', {'action':'revert'}) == {'acao':'reverter'}
    assert internal_arguments('avaliar_busca', {'questions':[{'query':'Where?', 'expected_paths':['src/file.py']}]}) == {'perguntas':[{'pergunta':'Where?', 'esperado':['src/file.py']}]}


def test_public_result_preserves_source_content_and_revision_maps():
    source = {'itens':[{'path':'Decisões/ativa.md','titulo':'Decisão ativa', 'conteudo':'Código chama imports', 'kind':'codigo'}], 'mais':True, 'revisao_plano':'abc', 'revisoes':{'conteudo':'hash'}, 'frontmatter':{'tipo':'decisao','status':'ativa'}}
    with language('en'):
        result = json.loads(public_result(json.dumps(source)))
    assert result['items'][0] == {'path':'Decisões/ativa.md','title':'Decisão ativa','content':'Código chama imports','kind':'code'}
    assert result['has_more'] and result['plan_revision']=='abc'
    assert result['revisions']=={'conteudo':'hash'} and result['frontmatter']==source['frontmatter']


def test_english_mcp_discovery_calls_and_legacy_aliases(indexado):
    vault, _ = indexado
    async def run():
        server = build_server(vault, auto_index=False)
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert set(tools) == {item[0] for item in TOOLS.values()}
        assert 'buscar' not in tools and 'search' in tools
        assert tools['task_context'].input_schema['properties']['objective']['default']=='implement'
        assert {'source','target','direction'} <= set(tools['graph_path'].input_schema['properties'])
        assert all('ctx' not in t.input_schema['properties'] for t in tools.values())
        english = await server.call_tool('symbol_location', {'symbol':'soma'})
        legacy = await server.call_tool('onde', {'simbolo':'soma'})
        assert english.content[0].text.startswith('**Definition of')
        assert legacy.content[0].text.startswith('**Definição de')
        assert 'src/util.py' in english.content[0].text and 'src/util.py' in legacy.content[0].text
        assert not is_english(), 'language must not leak into subsequent legacy or web calls'
        explained = await server.call_tool('explain', {'target':'src/app.py'})
        assert '**Imports' in explained.content[0].text and 'src/util.py' in explained.content[0].text
        graph = await server.call_tool('query_graph', {'query':'src/app.py::main','relations':['calls'],'direction':'outgoing'})
        assert 'EXTRACTED' in graph.content[0].text and 'src/util.py' in graph.content[0].text
        search = await server.call_tool('search', {'query':'folder:src type:code','limit':2})
        assert 'src/' in search.content[0].text
        read = await server.call_tool('read', {'note':'notas/Glossário.md'})
        assert 'Termos do projeto.' in read.content[0].text
        assert 'modified' in read.content[0].text
        report = await server.call_tool('report', {})
        assert '# atlasbrain — report for' in report.content[0].text
    asyncio.run(run())
