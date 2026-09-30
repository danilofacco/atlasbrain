import threading
from types import SimpleNamespace

import pytest

from atlasbrain import tools as f, graph as g, topology as t
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault, scan
from atlasbrain.search import Searcher


@pytest.fixture
def code(tmp_path):
    root = tmp_path / 'codigo'
    root.mkdir()
    files = {
        'core.py': 'def leaf():\n    return 1\ndef middle():\n    return leaf()\ndef entry():\n    return middle()\ndef unrelated():\n    return 0\n',
        'client.py': 'from core import leaf as calculate\nfrom external import leaf as external_leaf\ndef use():\n    return calculate()\ndef outside():\n    return external_leaf()\n',
        'namespace.py': 'import core as service\ndef use():\n    return service.leaf()\n',
        'types.py': 'class Alpha:\n    def leaf(self):\n        return 1\n    def run(self):\n        return self.leaf()\nclass Beta:\n    def leaf(self):\n        return 2\n    def run(self):\n        return self.leaf()\n',
        'nested.py': 'def first():\n    def inner():\n        return 1\n    return inner()\ndef second():\n    def inner():\n        return 2\n    return inner()\n',
        'a.ts': 'export function target() { return 1; }\nexport function decoy() { return 2; }\n',
        'b.ts': 'import { target as renamed } from "./a";\nimport * as service from "./a";\nexport function caller() { return renamed() + service.target(); }\n',
        'amb.py': 'def run():\n    return ghost()\n',
        'one.py': 'def ghost():\n    return 1\n',
        'two.py': 'def ghost():\n    return 2\n',
    }
    for path, text in files.items():
        (root / path).write_text(text)
    index_vault(root, quiet=True)
    con = connect(root)
    ctx = SimpleNamespace(vault=root, con=con, db_lock=threading.Lock(), searcher=Searcher(con))
    yield ctx
    con.close()


def node(G, target):
    found = t.identify(G, target)
    assert len(found) == 1, (target, found)
    return found[0]


def relations(G, src, dst):
    return list(G.get_edge_data(node(G, src), node(G, dst), default={}).values())


def test_todas_extensoes_do_parser_chegam_ao_scan(tmp_path):
    from atlasbrain.code import LANGS
    for ext in LANGS:
        (tmp_path / ('file' + ext)).write_text('source')
    assert {p.suffix for p in scan(tmp_path).values()} == set(LANGS)


def test_chamadas_locais_e_escopos(code):
    G = t.snapshot(code)
    assert relations(G, 'core.py::middle', 'core.py::leaf')[0]['conf'] == 'EXTRACTED'
    assert relations(G, 'core.py::entry', 'core.py::middle')
    assert relations(G, 'types.py::Alpha.run', 'types.py::Alpha.leaf')
    assert not relations(G, 'types.py::Alpha.run', 'types.py::Beta.leaf')
    assert relations(G, 'nested.py::first', 'nested.py::first.inner')
    assert relations(G, 'nested.py::second', 'nested.py::second.inner')
    assert not relations(G, 'nested.py::first', 'nested.py::second.inner')


def test_aliases_e_bibliotecas_externas(code):
    G = t.snapshot(code)
    for src in ('client.py::use', 'namespace.py::use', 'b.ts::caller'):
        dst = 'a.ts::target' if src.startswith('b.ts') else 'core.py::leaf'
        assert relations(G, src, dst)
        assert all(e['conf'] == 'EXTRACTED' for e in relations(G, src, dst))
    assert not relations(G, 'client.py::outside', 'core.py::leaf')
    assert not relations(G, 'b.ts::caller', 'a.ts::decoy')
    # Duas chamadas distintas ao mesmo símbolo mantêm evidências independentes.
    assert len(relations(G, 'b.ts::caller', 'a.ts::target')) == 2


def test_relacoes_paralelas_e_sentidos_opostos(code):
    con = code.con
    a = g.find_note(con, 'core.py')['id']
    b = g.find_note(con, 'client.py')['id']
    con.execute("INSERT INTO links(src,target,dst,kind,conf) VALUES(?,?,?,?,?)", (a, 'client.py', b, 'menciona', 'INFERRED'))
    con.commit()
    G = g.build_relations(con)
    assert {e['kind'] for e in G[b][a].values()} >= {'importa', 'chama'}
    assert G[a][b][next(iter(G[a][b]))]['kind'] == 'menciona'
    projection = g.build_graph(con, similar=False, ghosts=False)
    # A relação escolhida precisa levar sua própria procedência, não a da primeira aresta.
    assert projection[a][b]['conf'] == 'EXTRACTED'


def test_impacto_transitivo_com_evidencia(code):
    result = f.impact(code, 'core.py::leaf')
    assert '`middle`' in result and '`entry`' in result and '`use`' in result
    assert 'unrelated' not in result and 'outside' not in result
    assert 'evidência L4' in result and 'EXTRACTED' in result
    shallow = f.impact(code, 'core.py::leaf', depth=1)
    assert '`middle`' in shallow and '`entry`' not in shallow
    zero = f.impact(code, 'core.py::leaf', depth=0)
    assert 'Nenhum dependente' in zero
    file_result = f.impact(code, 'core.py')
    assert 'client.py' in file_result and 'namespace.py' in file_result


def test_nomes_ambiguos_exigem_escolha(code):
    assert 'ambíguo' in f.impact(code, 'leaf')
    assert 'Alpha.leaf' in f.impact(code, 'leaf')
    G = t.snapshot(code)
    assert not relations(G, 'amb.py::run', 'one.py::ghost')
    assert any(r['alvo'] == 'ghost' for r in G.graph['ambiguas'])
    assert 'ambígua' in f.query_graph(code, 'amb.py::run')


def test_bfs_dfs_direcao_e_filtros(code):
    for mode in ('bfs', 'dfs'):
        result = f.query_graph(code, 'core.py::entry', mode=mode, direction='saida', relations=['chama'])
        assert '`middle`' in result and '`leaf`' in result
        assert 'client.py' not in result
    result = f.query_graph(code, 'core.py::leaf', direction='entrada', relations=['chama'], depth=1)
    assert '`middle`' in result and '`entry`' not in result
    result = f.query_graph(code, 'core.py::entry', relations=[])
    assert '`entry`' in result and '`middle`' not in result
    result = f.query_graph(code, 'core.py::entry', tokens=128, depth=6)
    assert len(result.encode()) <= 128 * 4 and 'TRUNCADO' in result
    assert 'modo deve' in f.query_graph(code, 'core.py', mode='invalid')
    assert 'profundidade deve' in f.impact(code, 'core.py', depth=7)


def test_caminhos_seguem_direcao(code):
    forward = f.graph_path(code, 'core.py::entry', 'core.py::leaf', direction='saida')
    assert '2 salto(s)' in forward and 'chama' in forward
    reverse = f.graph_path(code, 'core.py::leaf', 'core.py::entry', direction='saida')
    assert 'Não há caminho' in reverse
    reverse = f.graph_path(code, 'core.py::leaf', 'core.py::entry', direction='entrada')
    assert '2 salto(s)' in reverse and 'EXTRACTED' in reverse


def test_cache_invalida_apos_edicao_e_remocao(code):
    original = t.snapshot(code)
    assert t.snapshot(code) is original
    (code.vault / 'client.py').write_text('def new_caller():\n    return 42\n')
    (code.vault / 'namespace.py').unlink()
    index_vault(code.vault, quiet=True)
    updated = t.snapshot(code)
    assert updated is not original
    assert not t.identify(updated, 'client.py::use')
    assert t.identify(updated, 'client.py::new_caller')
    assert not t.identify(updated, 'namespace.py')


def test_ciclos_nao_entram_em_loop(code):
    (code.vault / 'cycle.py').write_text('def alpha():\n    return beta()\ndef beta():\n    return alpha()\n')
    index_vault(code.vault, quiet=True)
    result = f.impact(code, 'cycle.py::alpha', depth=6)
    assert '`beta`' in result
    assert len(result) < 2000


def test_inferencias_sao_opcionais_no_impacto(code):
    (code.vault / 'guess.py').write_text('def inferred_caller():\n    return unrelated()\n')
    index_vault(code.vault, quiet=True)
    assert 'inferred_caller' not in f.impact(code, 'core.py::unrelated')
    result = f.impact(code, 'core.py::unrelated', include_inferred=True)
    assert 'inferred_caller' in result and 'INFERRED' in result


def test_callback_e_variavel_local_nao_viram_funcao_global(code):
    (code.vault / 'shadow.py').write_text(
        'from core import leaf\n'
        'def callback(leaf):\n    return leaf()\n'
        'def assigned():\n    leaf = get_callback()\n    return leaf()\n'
        'def local_definition():\n    def leaf():\n        return 7\n    return leaf()\n'
        'def x():\n    return 1\ndef y():\n    return x()\n'
    )
    index_vault(code.vault, quiet=True)
    G = t.snapshot(code)
    assert not relations(G, 'shadow.py::callback', 'core.py::leaf')
    assert not relations(G, 'shadow.py::assigned', 'core.py::leaf')
    assert relations(G, 'shadow.py::local_definition', 'shadow.py::local_definition.leaf')
    assert relations(G, 'shadow.py::y', 'shadow.py::x')


def test_python_metodos_exigem_receptor(code):
    (code.vault / 'scope.py').write_text(
        'def leaf():\n    return 3\nclass Scope:\n    def leaf(self):\n        return 1\n'
        '    def global_call(self):\n        return leaf()\n'
    )
    index_vault(code.vault, quiet=True)
    G = t.snapshot(code)
    assert relations(G, 'scope.py::Scope.global_call', 'scope.py::leaf')
    assert not relations(G, 'scope.py::Scope.global_call', 'scope.py::Scope.leaf')


def test_exports_default_nomeados_e_require(code):
    (code.vault / 'default.ts').write_text('export default function work() { return 1; }\nexport function decoy() { return 0; }\n')
    (code.vault / 'consumer.ts').write_text('import execute from "./default";\nconst core = require("./a");\nexport const caller = () => execute() + core.target();\n')
    index_vault(code.vault, quiet=True)
    G = t.snapshot(code)
    assert relations(G, 'consumer.ts::caller', 'default.ts::work')
    assert relations(G, 'consumer.ts::caller', 'a.ts::target')
    assert not relations(G, 'consumer.ts::caller', 'default.ts::decoy')


def test_imports_dentro_de_funcao_respeitam_escopo(code):
    (code.vault / 'scoped_import.py').write_text(
        'def local_import():\n    from core import leaf as calculate\n    return calculate()\n'
        'def external_import():\n    from external import leaf\n    return leaf()\n'
        'def local_namespace():\n    import core as service\n    return service.leaf()\n'
    )
    index_vault(code.vault, quiet=True)
    G = t.snapshot(code)
    assert relations(G, 'scoped_import.py::local_import', 'core.py::leaf')
    assert relations(G, 'scoped_import.py::local_namespace', 'core.py::leaf')
    assert not relations(G, 'scoped_import.py::external_import', 'core.py::leaf')
    result = f.symbol_location(code, 'core.py::leaf')
    assert 'local_import' in result and 'local_namespace' in result and 'external_import' not in result


def test_dfs_reexpande_quando_encontra_caminho_mais_curto():
    import networkx as nx
    G = nx.MultiDiGraph()
    G.add_edges_from([('root', 'deep'), ('root', 'short'), ('deep', 'long'),
                      ('long', 'shared'), ('short', 'shared'), ('shared', 'tail'), ('tail', 'leaf')],
                     kind='chama', conf='EXTRACTED')
    for mode in ('bfs', 'dfs'):
        depths, _, cut = t.traverse(G, ['root'], 4, 'saida', {'chama'}, mode=mode)
        assert depths['shared'] == 2 and depths['leaf'] == 4 and not cut


def test_impacto_classe_inclui_dependentes_dos_metodos(code):
    (code.vault / 'classes.py').write_text(
        'from types import Alpha\n'
        'class Derived(Alpha):\n    pass\n'
        'def execute():\n    return Alpha.leaf(None)\n'
    )
    index_vault(code.vault, quiet=True)
    result = f.impact(code, 'types.py::Alpha')
    assert 'execute' in result and 'Derived' in result
    assert 'Beta.run' not in result
